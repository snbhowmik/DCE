"""Funnel metrics per region × channel × period (IDEATION §7.2, ARCH §5.3, PRD FR-4).

All ratios go through `safe_div`: a zero or null denominator gives null, never inf/NaN.
Counts that are summed over days (unique_visitors) are period sums of daily uniques.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import polars as pl

Grain = Literal["week", "month"]
_TRUNC = {"week": "1w", "month": "1mo"}
KEYS = ("region_id", "channel", "period_start")


def safe_div(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    return pl.when(den.is_not_null() & (den != 0)).then(num / den).otherwise(None)


def period_expr(col: str, grain: Grain) -> pl.Expr:
    return pl.col(col).dt.truncate(_TRUNC[grain])


def period_grid(regions: pl.DataFrame, first: date, last: date, grain: Grain) -> pl.DataFrame:
    """Every region × channel × period in [first, last] (period starts)."""
    starts = pl.date_range(
        pl.lit(first).dt.truncate(_TRUNC[grain]),
        pl.lit(last).dt.truncate(_TRUNC[grain]),
        interval=_TRUNC[grain],
        eager=True,
    ).alias("period_start")
    return (
        regions.select("region_id")
        .join(pl.DataFrame({"channel": ["D2C", "B2B"]}), how="cross")
        .join(starts.to_frame(), how="cross")
    )


def _period_end(grain: Grain) -> pl.Expr:
    return (
        pl.col("period_start").dt.offset_by("1w" if grain == "week" else "1mo").dt.offset_by("-1d")
    )


def _marketing(mkt: pl.DataFrame, grain: Grain) -> pl.DataFrame:
    return (
        mkt.with_columns(period_expr("date", grain).alias("period_start"))
        .group_by(KEYS)
        .agg(
            pl.col(
                "spend_inr",
                "impressions",
                "clicks",
                "unique_visitors",
                "bounces",
                "leads",
                "qualified_leads",
                "conversions",
                "attributed_revenue_inr",
            ).sum()
        )
    )


def _orders(orders: pl.DataFrame, skus: pl.DataFrame, grain: Grain) -> pl.DataFrame:
    o = orders.join(skus.select("sku_id", "unit_cost_inr_per_kg"), on="sku_id", how="left")
    served = pl.col("fulfilled_qty_kg") > 0
    return (
        o.with_columns(period_expr("order_date", grain).alias("period_start"))
        .group_by(KEYS)
        .agg(
            served.sum().cast(pl.Int64).alias("n_orders"),
            pl.col("fulfilled_qty_kg").sum().alias("fulfilled_kg"),
            (pl.col("fulfilled_qty_kg") * pl.col("unit_price_inr")).sum().alias("revenue_inr"),
            (
                pl.col("fulfilled_qty_kg")
                * (pl.col("unit_price_inr") - pl.col("unit_cost_inr_per_kg"))
            )
            .sum()
            .alias("gross_margin_inr"),
            pl.coalesce("customer_id", "account_id")
            .filter(served)
            .n_unique()
            .alias("ordering_customers"),
        )
    )


def _customer_flows(customers: pl.DataFrame, grid: pl.DataFrame, grain: Grain) -> pl.DataFrame:
    """D2C new / at-start / lost / active per region × period via cumulative counts."""
    c = customers.with_columns(
        period_expr("acquired_date", grain).alias("acq_p"),
        period_expr("churned_date", grain).alias("churn_p"),
    )
    periods = grid.filter(pl.col("channel") == "D2C").select("region_id", "period_start")
    acq = c.group_by("region_id", "acq_p").agg(
        pl.len().alias("new_customers"),
        pl.col("acquisition_campaign_type").is_not_null().sum().alias("new_customers_paid"),
    )
    churn = (
        c.filter(pl.col("churn_p").is_not_null())
        .group_by("region_id", "churn_p")
        .agg(
            pl.len().alias("churned"),
            (pl.col("churn_p") == pl.col("acq_p")).sum().alias("churned_same_period"),
        )
    )
    # Cumulative counts strictly before each period start, including pre-window history.
    before = (
        periods.join(acq, on="region_id", how="left")
        .filter(pl.col("acq_p") < pl.col("period_start"))
        .group_by("region_id", "period_start")
        .agg(pl.col("new_customers").sum().alias("acq_before"))
    )
    churn_before = (
        periods.join(churn, on="region_id", how="left")
        .filter(pl.col("churn_p") < pl.col("period_start"))
        .group_by("region_id", "period_start")
        .agg(pl.col("churned").sum().alias("churn_before"))
    )
    out = (
        periods.join(before, on=["region_id", "period_start"], how="left")
        .join(churn_before, on=["region_id", "period_start"], how="left")
        .join(acq.rename({"acq_p": "period_start"}), on=["region_id", "period_start"], how="left")
        .join(
            churn.rename({"churn_p": "period_start"}),
            on=["region_id", "period_start"],
            how="left",
        )
        .fill_null(0)
        .with_columns(
            (pl.col("acq_before") - pl.col("churn_before")).alias("customers_at_start"),
            (pl.col("churned") - pl.col("churned_same_period")).alias("customers_lost"),
        )
        .with_columns(
            (pl.col("customers_at_start") + pl.col("new_customers")).alias("active_customers"),
            pl.lit("D2C").alias("channel"),
        )
    )
    return out.select(
        *KEYS,
        "new_customers",
        "new_customers_paid",
        "customers_at_start",
        "customers_lost",
        "active_customers",
    )


def _b2b_flows(
    accounts: pl.DataFrame, skus: pl.DataFrame, grid: pl.DataFrame, grain: Grain
) -> pl.DataFrame:
    """B2B counterpart: accounts as customers; contract dates define activity; MRR."""
    unit_cost = skus.filter(pl.col("status") == "production")["unit_cost_inr_per_kg"].mean()
    a = accounts.filter(pl.col("contract_start").is_not_null()).with_columns(
        period_expr("onboarded_date", grain).alias("onb_p"),
        pl.when(pl.col("status") == "churned").then(pl.col("contract_end")).alias("churn_date"),
    )
    periods = (
        grid.filter(pl.col("channel") == "B2B")
        .select("region_id", "period_start")
        .with_columns(_period_end(grain).alias("period_end"))
    )
    j = periods.join(a, on="region_id", how="left")
    live = (pl.col("contract_start") <= pl.col("period_end")) & (
        pl.col("contract_end").is_null() | (pl.col("contract_end") >= pl.col("period_start"))
    )
    at_start = (pl.col("contract_start") < pl.col("period_start")) & (
        pl.col("churn_date").is_null() | (pl.col("churn_date") >= pl.col("period_start"))
    )
    lost = at_start & pl.col("churn_date").is_between(pl.col("period_start"), pl.col("period_end"))
    monthly_margin = pl.col("committed_kg_per_month") * (
        pl.col("contract_price_inr_per_kg") - unit_cost
    )
    tenure_months = (pl.col("contract_end") - pl.col("contract_start")).dt.total_days() / 30.4375
    return (
        j.group_by("region_id", "period_start")
        .agg(
            (pl.col("onb_p") == pl.col("period_start")).sum().alias("new_customers"),
            (pl.col("onb_p") == pl.col("period_start")).sum().alias("new_customers_paid"),
            at_start.sum().alias("customers_at_start"),
            lost.sum().alias("customers_lost"),
            live.sum().alias("active_customers"),
            (pl.col("committed_kg_per_month") * pl.col("contract_price_inr_per_kg"))
            .filter(live)
            .sum()
            .alias("b2b_contract_mrr_inr"),
            (monthly_margin * tenure_months).filter(live).mean().alias("b2b_ltv_inr"),
        )
        .with_columns(pl.lit("B2B").alias("channel"))
    )


def _nps(nps: pl.DataFrame, grain: Grain) -> pl.DataFrame:
    return (
        nps.with_columns(period_expr("response_date", grain).alias("period_start"))
        .group_by(KEYS)
        .agg(
            pl.len().alias("nps_responses"),
            (pl.col("score") >= 9).sum().alias("promoters"),
            (pl.col("score") <= 6).sum().alias("detractors"),
        )
    )


def _subscriber_revenue(
    orders: pl.DataFrame, customers: pl.DataFrame, grain: Grain
) -> pl.DataFrame:
    return (
        orders.join(customers.select("customer_id", "is_subscriber"), on="customer_id", how="inner")
        .filter(pl.col("is_subscriber"))
        .with_columns(period_expr("order_date", grain).alias("period_start"))
        .group_by("region_id", "period_start")
        .agg(
            (pl.col("fulfilled_qty_kg") * pl.col("unit_price_inr"))
            .sum()
            .alias("d2c_subscription_revenue_inr")
        )
        .with_columns(pl.lit("D2C").alias("channel"))
    )


def funnel_metrics(
    tables: dict[str, pl.DataFrame], first: date, last: date, grain: Grain = "month"
) -> pl.DataFrame:
    """One row per region × channel × period between `first` and `last` (inclusive)."""
    grid = period_grid(tables["regions"], first, last, grain)
    customers = tables["customers"]
    flow_cols = [
        "new_customers", "new_customers_paid", "customers_at_start", "customers_lost",
        "active_customers",
    ]  # fmt: skip
    b2b = _b2b_flows(tables["b2b_accounts"], tables["skus"], grid, grain)
    flows = pl.concat(
        [_customer_flows(customers, grid, grain), b2b.select(*KEYS, *flow_cols)],
        how="vertical_relaxed",
    )
    b2b_extra = b2b.select(*KEYS, "b2b_contract_mrr_inr", "b2b_ltv_inr")
    df = (
        grid.join(_marketing(tables["marketing_daily"], grain), on=KEYS, how="left")
        .join(_orders(tables["orders"], tables["skus"], grain), on=KEYS, how="left")
        .join(flows, on=KEYS, how="left")
        .join(_nps(tables["nps_responses"], grain), on=KEYS, how="left")
        .join(b2b_extra, on=KEYS, how="left")
        .join(_subscriber_revenue(tables["orders"], customers, grain), on=KEYS, how="left")
    )
    count_cols = [
        "spend_inr", "impressions", "clicks", "unique_visitors", "bounces", "leads",
        "qualified_leads", "conversions", "attributed_revenue_inr", "n_orders", "fulfilled_kg",
        "revenue_inr", "gross_margin_inr", "ordering_customers", "new_customers",
        "new_customers_paid", "customers_at_start", "customers_lost", "active_customers",
        "nps_responses", "promoters", "detractors",
    ]  # fmt: skip
    df = df.with_columns(pl.col(count_cols).fill_null(0))

    margin_pct = safe_div(pl.col("gross_margin_inr"), pl.col("revenue_inr"))
    churn = safe_div(pl.col("customers_lost"), pl.col("customers_at_start"))
    cac = safe_div(pl.col("spend_inr"), pl.col("new_customers"))
    aov = safe_div(pl.col("revenue_inr"), pl.col("n_orders"))
    freq = safe_div(pl.col("n_orders"), pl.col("active_customers"))
    d2c_ltv = aov * freq * margin_pct * safe_div(pl.lit(1.0), churn)
    ltv = pl.when(pl.col("channel") == "D2C").then(d2c_ltv).otherwise(pl.col("b2b_ltv_inr"))
    mrr = (
        pl.when(pl.lit(grain == "month"))
        .then(
            pl.col("b2b_contract_mrr_inr").fill_null(0)
            + pl.col("d2c_subscription_revenue_inr").fill_null(0)
        )
        .otherwise(None)
    )

    df = df.with_columns(
        safe_div(pl.col("bounces"), pl.col("unique_visitors")).alias("bounce_rate"),
        safe_div(pl.col("clicks"), pl.col("impressions")).alias("ctr"),
        safe_div(pl.col("spend_inr"), pl.col("clicks")).alias("cpc_inr"),
        safe_div(pl.col("spend_inr"), pl.col("leads")).alias("cpl_inr"),
        safe_div(pl.col("qualified_leads"), pl.col("leads")).alias("qualified_rate"),
        safe_div(pl.col("leads"), pl.col("unique_visitors")).alias("visitor_to_lead"),
        safe_div(pl.col("conversions"), pl.col("leads")).alias("lead_to_customer"),
        cac.alias("cac_inr"),
        safe_div(pl.col("spend_inr"), pl.col("new_customers_paid")).alias("cac_paid_inr"),
        safe_div(
            pl.col("new_customers") - pl.col("new_customers_paid"), pl.col("new_customers")
        ).alias("organic_share"),
        churn.alias("churn_rate"),
        aov.alias("aov_inr"),
        freq.alias("purchase_frequency"),
        margin_pct.alias("gross_margin_pct"),
        ltv.alias("ltv_inr"),
        mrr.alias("mrr_inr"),
        (100 * safe_div(pl.col("promoters") - pl.col("detractors"), pl.col("nps_responses"))).alias(
            "nps"
        ),
        safe_div(
            pl.col("attributed_revenue_inr") * margin_pct - pl.col("spend_inr"),
            pl.col("spend_inr"),
        ).alias("roi"),
    ).with_columns(safe_div(pl.col("ltv_inr"), pl.col("cac_inr")).alias("ltv_cac"))
    return df.drop("b2b_contract_mrr_inr", "d2c_subscription_revenue_inr", "b2b_ltv_inr").sort(KEYS)

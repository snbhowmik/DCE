"""Cohort-based D2C LTV (ARCH §5.3).

Cohort = region × acquisition month. For each cohort and age k (months since acquisition):
  alive_k   customers not churned before the start of age k
  lost_k    of those, churned during age k
  margin_k  Σ fulfilled_kg × (unit_price − unit_cost) of the cohort's orders in age k

realized_ltv  = Σ_k margin_k / n
projected_ltv = realized_ltv + alive_share_now × m̄ × min(1/h, cap)
  m̄ = Σ margin_k / Σ alive_k   over the last `window` complete ages (margin per alive month)
  h  = Σ lost_k / Σ alive_k     over the same ages (monthly churn hazard)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from dce.metrics.funnel import safe_div


@dataclass(frozen=True)
class LtvConfig:
    max_lifetime_months: float = 36.0
    window_months: int = 3
    min_cohort_age_months: int = 3

    @classmethod
    def from_scoring(cls, scoring: dict[str, Any]) -> LtvConfig:
        m = scoring.get("metrics", {})
        return cls(
            max_lifetime_months=float(m.get("ltv_max_lifetime_months", cls.max_lifetime_months)),
            window_months=int(m.get("ltv_projection_window_months", cls.window_months)),
            min_cohort_age_months=int(
                m.get("ltv_min_cohort_age_months", cls.min_cohort_age_months)
            ),
        )


def _month_index(col: str) -> pl.Expr:
    return pl.col(col).dt.year() * 12 + pl.col(col).dt.month() - 1


def cohort_table(tables: dict[str, pl.DataFrame], last_complete_month: date) -> pl.DataFrame:
    """Long table: region × cohort_month × age with n, alive, lost, margin."""
    last_idx = last_complete_month.year * 12 + last_complete_month.month - 1
    cust = (
        tables["customers"]
        .with_columns(
            pl.col("acquired_date").dt.truncate("1mo").alias("cohort_month"),
            _month_index("acquired_date").alias("acq_idx"),
            _month_index("churned_date").alias("churn_idx"),
        )
        .filter(pl.col("acq_idx") <= last_idx)
    )
    if cust.is_empty():
        return pl.DataFrame(
            schema={
                "region_id": pl.String, "cohort_month": pl.Date, "age": pl.Int64,
                "n": pl.UInt32, "alive": pl.UInt32, "lost": pl.UInt32, "margin_inr": pl.Float64,
            }
        )  # fmt: skip
    sizes = cust.group_by("region_id", "cohort_month", "acq_idx").agg(pl.len().alias("n"))
    ages = sizes.with_columns(
        pl.int_ranges(0, last_idx - pl.col("acq_idx") + 1).alias("age")
    ).explode("age", empty_as_null=False)

    survival = (
        ages.join(cust.select("region_id", "cohort_month", "churn_idx", "acq_idx"),
                  on=["region_id", "cohort_month", "acq_idx"])
        .with_columns((pl.col("acq_idx") + pl.col("age")).alias("idx"))
        .group_by("region_id", "cohort_month", "age")
        .agg(
            (pl.col("churn_idx").is_null() | (pl.col("churn_idx") >= pl.col("idx")))
            .sum()
            .alias("alive"),
            (pl.col("churn_idx") == pl.col("idx")).sum().alias("lost"),
        )
    )  # fmt: skip

    unit_cost = tables["skus"].select("sku_id", "unit_cost_inr_per_kg")
    margin = (
        tables["orders"]
        .filter(pl.col("channel") == "D2C")
        .join(unit_cost, on="sku_id", how="left")
        .join(cust.select("customer_id", "cohort_month", "acq_idx"), on="customer_id")
        .with_columns(
            (_month_index("order_date") - pl.col("acq_idx")).alias("age"),
            (
                pl.col("fulfilled_qty_kg")
                * (pl.col("unit_price_inr") - pl.col("unit_cost_inr_per_kg"))
            ).alias("m"),
        )
        .filter(pl.col("age").is_between(0, last_idx - pl.col("acq_idx")))
        .group_by("region_id", "cohort_month", "age")
        .agg(pl.col("m").sum().alias("margin_inr"))
    )
    return (
        ages.drop("acq_idx")
        .join(survival, on=["region_id", "cohort_month", "age"], how="left")
        .join(margin, on=["region_id", "cohort_month", "age"], how="left")
        .with_columns(
            pl.col("margin_inr").fill_null(0.0),
            pl.col("age", "n", "alive", "lost").cast(pl.Int64),
        )
        .sort("region_id", "cohort_month", "age")
    )


def cohort_ltv(cohorts: pl.DataFrame, cfg: LtvConfig | None = None) -> pl.DataFrame:
    """One row per region × cohort with realized and projected LTV per acquired customer."""
    cfg = cfg or LtvConfig()
    max_age = pl.col("age").max().over("region_id", "cohort_month")
    in_window = pl.col("age") > max_age - cfg.window_months
    g = cohorts.group_by("region_id", "cohort_month").agg(
        pl.col("n").first(),
        pl.col("age").max().alias("age_months"),
        pl.col("margin_inr").sum().alias("margin_total"),
        pl.col("margin_inr").filter(in_window).sum().alias("w_margin"),
        pl.col("alive").filter(in_window).sum().alias("w_alive"),
        pl.col("lost").filter(in_window).sum().alias("w_lost"),
        (pl.col("alive") - pl.col("lost")).sort_by("age").last().alias("alive_now"),
    )
    m_bar = safe_div(pl.col("w_margin"), pl.col("w_alive"))
    hazard = safe_div(pl.col("w_lost"), pl.col("w_alive"))
    lifetime = (
        pl.when(hazard.is_null() | (hazard == 0))
        .then(pl.lit(cfg.max_lifetime_months))
        .otherwise(pl.min_horizontal(1.0 / hazard, pl.lit(cfg.max_lifetime_months)))
    )
    return (
        g.with_columns(
            safe_div(pl.col("margin_total"), pl.col("n")).alias("realized_ltv_inr"),
            m_bar.alias("monthly_margin_per_alive_inr"),
            hazard.alias("monthly_churn"),
            safe_div(pl.col("alive_now"), pl.col("n")).alias("alive_share"),
        )
        .with_columns(
            (
                pl.col("realized_ltv_inr")
                + pl.col("alive_share") * pl.col("monthly_margin_per_alive_inr").fill_null(0.0)
                * lifetime
            ).alias("projected_ltv_inr")
        )
        .select(
            "region_id", "cohort_month", "n", "age_months", "realized_ltv_inr",
            "monthly_margin_per_alive_inr", "monthly_churn", "alive_share", "projected_ltv_inr",
        )
        .sort("region_id", "cohort_month")
    )  # fmt: skip


def region_ltv(ltv: pl.DataFrame, cfg: LtvConfig | None = None) -> pl.DataFrame:
    """Size-weighted projected LTV per region over cohorts at least `min_cohort_age` old."""
    cfg = cfg or LtvConfig()
    mature = ltv.filter(pl.col("age_months") >= cfg.min_cohort_age_months)
    return (
        mature.group_by("region_id")
        .agg(
            safe_div((pl.col("projected_ltv_inr") * pl.col("n")).sum(), pl.col("n").sum()).alias(
                "ltv_inr"
            ),
            pl.col("n").sum().alias("n_customers"),
            pl.len().alias("n_cohorts"),
        )
        .sort("region_id")
    )

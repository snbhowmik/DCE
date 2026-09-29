"""Weekly unconstrained demand vs. fulfilled sales (ARCH §5.2, D-005, D-018).

demand_kg   = Σ requested_qty_kg of orders that needed capacity (all statuses except
              `cancelled_other`, which is customer-initiated and never consumed capacity)
sales_kg    = Σ fulfilled_qty_kg (reporting only; never a forecasting target)
censored    = any waitlisted or cancelled_stockout volume in the week: realized orders may
              understate true demand, so forecasters treat the week as a lower bound
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from dce.numerics import SUM_DECIMALS

CENSORING_STATUSES = ("waitlisted", "cancelled_stockout")
EXCLUDED_STATUSES = ("cancelled_other",)
SERIES_KEYS = ("channel", "region_id", "sku_id", "account_id")

DEMAND_SCHEMA = pl.Schema(
    {
        "series_id": pl.String,
        "week_start": pl.Date,
        "channel": pl.String,
        "region_id": pl.String,
        "sku_id": pl.String,
        "account_id": pl.String,
        "demand_kg": pl.Float64,
        "sales_kg": pl.Float64,
        "unmet_kg": pl.Float64,
        "censored_kg": pl.Float64,
        "partial_short_kg": pl.Float64,
        "cancelled_other_kg": pl.Float64,
        "n_orders": pl.UInt32,
        "censored_share": pl.Float64,
        "is_censored": pl.Boolean,
    }
)


def series_id_expr() -> pl.Expr:
    """`D2C|<region>|<sku>` or `B2B|<account>|<sku>|<region>`."""
    return (
        pl.when(pl.col("channel") == "B2B")
        .then(
            pl.concat_str(
                [pl.lit("B2B"), pl.col("account_id"), pl.col("sku_id"), pl.col("region_id")],
                separator="|",
            )
        )
        .otherwise(
            pl.concat_str([pl.lit("D2C"), pl.col("region_id"), pl.col("sku_id")], separator="|")
        )
    )


def weekly_demand(orders: pl.DataFrame, last_week: date) -> pl.DataFrame:
    """One row per series × week, zero-filled from each series' first order week to `last_week`.

    `last_week` is the Monday of the final history week; later orders are dropped.
    """
    last_day = last_week + timedelta(days=6)
    o = orders.filter(pl.col("order_date") <= last_day).with_columns(
        pl.col("order_date").dt.truncate("1w").alias("week_start"),
        # D2C series are not split by customer; keep account_id only for B2B.
        pl.when(pl.col("channel") == "B2B").then(pl.col("account_id")).alias("account_id"),
    )
    needed = ~pl.col("status").is_in(EXCLUDED_STATUSES)
    agg = o.group_by([*SERIES_KEYS, "week_start"]).agg(
        pl.col("requested_qty_kg").filter(needed).sum().round(SUM_DECIMALS).alias("demand_kg"),
        pl.col("fulfilled_qty_kg").filter(needed).sum().round(SUM_DECIMALS).alias("sales_kg"),
        pl.col("requested_qty_kg")
        .filter(pl.col("status").is_in(CENSORING_STATUSES))
        .sum()
        .round(SUM_DECIMALS)
        .alias("censored_kg"),
        (pl.col("requested_qty_kg") - pl.col("fulfilled_qty_kg"))
        .filter(pl.col("status") == "partial")
        .sum()
        .round(SUM_DECIMALS)
        .alias("partial_short_kg"),
        pl.col("requested_qty_kg")
        .filter(pl.col("status").is_in(EXCLUDED_STATUSES))
        .sum()
        .round(SUM_DECIMALS)
        .alias("cancelled_other_kg"),
        needed.sum().cast(pl.UInt32).alias("n_orders"),
    )
    if agg.is_empty():
        return pl.DataFrame(schema=DEMAND_SCHEMA)

    spans = agg.group_by(SERIES_KEYS).agg(pl.col("week_start").min().alias("first"))
    grid = spans.with_columns(
        pl.date_ranges(pl.col("first"), pl.lit(last_week), interval="1w").alias("week_start")
    ).explode("week_start", empty_as_null=False)
    out = (
        grid.drop("first")
        .join(agg, on=[*SERIES_KEYS, "week_start"], how="left", nulls_equal=True)
        .with_columns(
            pl.col(
                "demand_kg", "sales_kg", "censored_kg", "partial_short_kg", "cancelled_other_kg"
            ).fill_null(0.0),
            pl.col("n_orders").fill_null(0),
        )
        .with_columns(
            (pl.col("demand_kg") - pl.col("sales_kg")).clip(lower_bound=0).alias("unmet_kg"),
            pl.when(pl.col("demand_kg") > 0)
            .then(pl.col("censored_kg") / pl.col("demand_kg"))
            .otherwise(0.0)
            .alias("censored_share"),
            series_id_expr().alias("series_id"),
        )
        .with_columns((pl.col("censored_share") > 0).alias("is_censored"))
    )
    out = out.select(DEMAND_SCHEMA.names()).cast(DEMAND_SCHEMA)
    return out.sort("series_id", "week_start")


def channel_totals(demand: pl.DataFrame) -> pl.DataFrame:
    """Bottom-up weekly totals per channel (for reporting and capacity comparison)."""
    return (
        demand.group_by("channel", "week_start")
        .agg(
            pl.col("demand_kg", "sales_kg", "unmet_kg", "censored_kg").sum().round(SUM_DECIMALS),
            pl.col("is_censored").any(),
        )
        .sort("channel", "week_start")
    )

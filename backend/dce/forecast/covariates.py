"""Covariates known at forecast time (ARCH §5.4).

Only sources a planner knows in advance are used: `marketing_plan` (planned spend), region tier,
list/contract prices, and the public holiday calendar. Realized marketing (`marketing_daily`) and
realized orders never enter this frame.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from dce.forecast.calendar import weekly_holidays
from dce.numerics import SUM_DECIMALS

KNOWN_SOURCES = ("marketing_plan", "regions", "skus", "b2b_accounts")


def series_meta(demand: pl.DataFrame) -> pl.DataFrame:
    """series_id → channel, region_id, sku_id, account_id (from the reconstructed demand)."""
    return demand.select("series_id", "channel", "region_id", "sku_id", "account_id").unique(
        "series_id", keep="first", maintain_order=True
    )


def build_covariates(
    tables: dict[str, pl.DataFrame], meta: pl.DataFrame, first_week: date, last_week: date
) -> pl.DataFrame:
    """One row per series × week in [first_week, last_week]:
    `series_id, week_start, planned_spend_inr, tier, channel, price_inr, holiday_count,
    festival_week`.
    """
    weeks = pl.date_range(first_week, last_week, interval="1w", eager=True).alias("week_start")
    plan = (
        tables["marketing_plan"]
        .group_by("week_start", "region_id", "channel")
        .agg(pl.col("planned_spend_inr").sum().round(SUM_DECIMALS))
    )
    tier = tables["regions"].select("region_id", "tier")
    list_price = tables["skus"].select("sku_id", pl.col("list_price_d2c_inr_per_kg").alias("lp"))
    contract = tables["b2b_accounts"].select(
        "account_id", pl.col("contract_price_inr_per_kg").alias("cp")
    )
    base = (
        meta.join(weeks.to_frame(), how="cross")
        .join(plan, on=["week_start", "region_id", "channel"], how="left")
        .join(tier, on="region_id", how="left")
        .join(list_price, on="sku_id", how="left")
        .join(contract, on="account_id", how="left")
        .join(weekly_holidays(first_week, last_week), on="week_start", how="left")
    )
    return base.select(
        "series_id",
        "week_start",
        pl.col("planned_spend_inr").fill_null(0.0),
        pl.col("tier").fill_null("unknown"),
        "channel",
        pl.when(pl.col("channel") == "B2B")
        .then(pl.coalesce("cp", "lp"))
        .otherwise(pl.col("lp"))
        .alias("price_inr"),
        pl.col("holiday_count").fill_null(0),
        pl.col("festival_week").fill_null(False),
    ).sort("series_id", "week_start")

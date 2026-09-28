"""T1.2: unconstrained demand vs. sales, censoring flags, zero-filling."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest

from dce.demand.reconstruct import channel_totals, weekly_demand

STOCKOUT_WEEK = date(2024, 1, 1) + timedelta(weeks=50)
WAITLIST_WEEK = STOCKOUT_WEEK + timedelta(weeks=1)


@pytest.fixture(scope="module")
def demand(tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]) -> pl.DataFrame:
    return weekly_demand(tiny_tables["orders"], tiny_window[1])


def _row(demand: pl.DataFrame, series: str, week: date) -> dict[str, Any]:
    return demand.filter((pl.col("series_id") == series) & (pl.col("week_start") == week)).row(
        0, named=True
    )


def test_stockout_week_shows_demand_above_sales(demand: pl.DataFrame) -> None:
    r = _row(demand, "D2C|R_N|SKU_CC_500", STOCKOUT_WEEK)
    assert r["demand_kg"] > r["sales_kg"]
    assert r["is_censored"] and r["censored_kg"] > 0
    assert r["unmet_kg"] == pytest.approx(r["demand_kg"] - r["sales_kg"])


def test_waitlist_week_is_censored(demand: pl.DataFrame) -> None:
    assert _row(demand, "D2C|R_N|SKU_CC_500", WAITLIST_WEEK)["is_censored"]


def test_normal_weeks_uncensored_and_equal(demand: pl.DataFrame) -> None:
    normal = demand.filter(
        (pl.col("channel") == "D2C") & ~pl.col("week_start").is_in([STOCKOUT_WEEK, WAITLIST_WEEK])
    )
    assert not normal["is_censored"].any()
    assert float((normal["demand_kg"] - normal["sales_kg"]).abs().max()) < 1e-9  # type: ignore[arg-type]


def test_b2b_partial_fill_is_unmet_but_not_censored(demand: pl.DataFrame) -> None:
    r = _row(demand, "B2B|ACC_DIST|SKU_CC_500|R_N", STOCKOUT_WEEK)
    assert r["unmet_kg"] > 0 and r["partial_short_kg"] > 0
    assert not r["is_censored"]


def test_zero_filled_grid(demand: pl.DataFrame, tiny_window: tuple[date, date]) -> None:
    counts = dict(demand.group_by("series_id").len().iter_rows())
    assert counts["D2C|R_N|SKU_CC_500"] == 110
    assert counts["D2C|R_S|SKU_CC_500"] == 110
    # ACC_REST contract starts 2024-03-04 (week 9) → 101 weeks
    assert counts["B2B|ACC_REST|SKU_CC_500|R_S"] == 101
    assert demand["week_start"].max() == tiny_window[1]
    assert (
        demand.filter(pl.col("channel") == "D2C")["account_id"].null_count()
        == demand.filter(pl.col("channel") == "D2C").height
    )


def test_totals_conserve_requested(
    demand: pl.DataFrame, tiny_tables: dict[str, pl.DataFrame]
) -> None:
    assert demand["demand_kg"].sum() == pytest.approx(
        tiny_tables["orders"]["requested_qty_kg"].sum()
    )
    tot = channel_totals(demand)
    assert tot["demand_kg"].sum() == pytest.approx(demand["demand_kg"].sum())


def test_cancelled_other_excluded_and_gaps_filled() -> None:
    d = date(2025, 1, 6)
    orders = pl.DataFrame(
        {
            "order_date": [d, d, d + timedelta(weeks=2)],
            "channel": ["D2C"] * 3,
            "region_id": ["R"] * 3,
            "sku_id": ["S"] * 3,
            "customer_id": ["c1", "c2", "c1"],
            "account_id": [None, None, None],
            "requested_qty_kg": [2.0, 5.0, 1.0],
            "fulfilled_qty_kg": [2.0, 0.0, 1.0],
            "status": ["fulfilled", "cancelled_other", "fulfilled"],
        },
        schema_overrides={"account_id": pl.String},
    )
    out = weekly_demand(orders, d + timedelta(weeks=3))
    assert out["demand_kg"].to_list() == [2.0, 0.0, 1.0, 0.0]
    assert out["cancelled_other_kg"].to_list() == [5.0, 0.0, 0.0, 0.0]
    assert not out["is_censored"].any()


def test_empty_orders() -> None:
    empty = pl.DataFrame(
        schema={
            "order_date": pl.Date, "channel": pl.String, "region_id": pl.String,
            "sku_id": pl.String, "account_id": pl.String, "requested_qty_kg": pl.Float64,
            "fulfilled_qty_kg": pl.Float64, "status": pl.String,
        }
    )  # fmt: skip
    assert weekly_demand(empty, date(2025, 1, 6)).is_empty()

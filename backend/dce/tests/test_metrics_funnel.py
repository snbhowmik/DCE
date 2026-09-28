"""T1.3: funnel metrics against hand-computed values; zero denominators give null."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest

from dce.metrics.cohorts import LtvConfig, cohort_ltv, cohort_table, region_ltv
from dce.metrics.funnel import funnel_metrics

D = date.fromisoformat


def _mkt(d: str, ch: str, spend: float, imp: int, clk: int, uv: int, bnc: int, leads: int,
         ql: int, conv: int, attr: float) -> dict[str, Any]:  # fmt: skip
    return {
        "date": D(d), "region_id": "R", "channel": ch, "campaign_type": "ppc",
        "campaign_id": "K", "spend_inr": spend, "impressions": imp, "clicks": clk,
        "unique_visitors": uv, "bounces": bnc, "leads": leads, "qualified_leads": ql,
        "conversions": conv, "attributed_revenue_inr": attr,
    }  # fmt: skip


def _order(
    oid: str, d: str, cid: str, req: float, ful: float, status: str = "fulfilled"
) -> dict[str, Any]:
    return {
        "order_id": oid, "order_date": D(d), "channel": "D2C", "region_id": "R", "sku_id": "S",
        "customer_id": cid, "account_id": None, "requested_qty_kg": req, "fulfilled_qty_kg": ful,
        "unit_price_inr": 300.0, "status": status,
    }  # fmt: skip


def _cust(
    cid: str, acq: str, camp: str | None, churn: str | None, sub: bool = False
) -> dict[str, Any]:
    return {
        "customer_id": cid, "region_id": "R", "acquired_date": D(acq),
        "acquisition_campaign_type": camp, "persona_segment": "p",
        "churned_date": D(churn) if churn else None, "is_subscriber": sub,
    }  # fmt: skip


def _acct(
    aid: str, onb: str, start: str, end: str | None, kg: float, price: float
) -> dict[str, Any]:
    return {
        "account_id": aid, "account_type": "restaurant", "region_id": "R", "regions_served": "R",
        "outlets_count": 1, "status": "active", "onboarded_date": D(onb),
        "contract_start": D(start), "contract_end": D(end) if end else None,
        "committed_kg_per_month": kg, "contract_price_inr_per_kg": price,
        "shortfall_penalty_inr_per_kg": 10.0, "requested_start_date": None,
        "requested_kg_per_month": None,
    }  # fmt: skip


@pytest.fixture(scope="module")
def world() -> dict[str, pl.DataFrame]:
    return {
        "regions": pl.DataFrame({"region_id": ["R", "R2"]}),
        "skus": pl.DataFrame(
            {"sku_id": ["S"], "unit_cost_inr_per_kg": [100.0], "status": ["production"]}
        ),
        "marketing_daily": pl.DataFrame(
            [
                _mkt("2025-01-05", "D2C", 1000, 10000, 200, 100, 40, 20, 10, 4, 2000),
                _mkt("2025-01-20", "D2C", 1000, 10000, 300, 100, 20, 30, 20, 6, 3000),
                _mkt("2025-02-10", "B2B", 500, 1000, 10, 10, 1, 2, 1, 1, 0),
            ]
        ),
        "customers": pl.DataFrame(
            [
                _cust("c1", "2024-12-10", "ppc", "2025-01-15"),
                _cust("c2", "2024-12-20", None, None, sub=True),
                _cust("c3", "2025-01-05", "ppc", None),
                _cust("c4", "2025-01-25", None, "2025-01-28"),
                _cust("c5", "2025-02-03", "ppc", None),
            ]
        ),
        "orders": pl.DataFrame(
            [
                _order("o1", "2025-01-06", "c2", 2, 2),
                _order("o2", "2025-01-10", "c3", 1, 1),
                _order("o3", "2025-01-12", "c1", 1, 0, "cancelled_stockout"),
                _order("o4", "2025-01-26", "c4", 1, 1),
                _order("o5", "2025-02-05", "c5", 2, 2),
            ],
            schema_overrides={"account_id": pl.String},
        ),
        "nps_responses": pl.DataFrame(
            {
                "response_date": [D("2025-01-10")] * 4,
                "channel": ["D2C"] * 4,
                "region_id": ["R"] * 4,
                "customer_or_account_id": ["c1", "c2", "c3", "c4"],
                "score": [10, 9, 7, 3],
            }
        ),
        "b2b_accounts": pl.DataFrame(
            [
                _acct("A1", "2024-10-15", "2024-11-01", "2025-10-31", 100, 250),
                _acct("A2", "2025-02-01", "2025-02-01", None, 50, 200),
            ]
        ),
    }


@pytest.fixture(scope="module")
def monthly(world: dict[str, pl.DataFrame]) -> pl.DataFrame:
    return funnel_metrics(world, D("2025-01-01"), D("2025-02-28"), "month")


def _get(df: pl.DataFrame, region: str, ch: str, period: str) -> dict[str, Any]:
    return df.filter(
        (pl.col("region_id") == region)
        & (pl.col("channel") == ch)
        & (pl.col("period_start") == D(period))
    ).row(0, named=True)


def test_grid_is_complete(monthly: pl.DataFrame) -> None:
    assert monthly.height == 2 * 2 * 2  # regions × channels × months


def test_top_of_funnel_ratios(monthly: pl.DataFrame) -> None:
    r = _get(monthly, "R", "D2C", "2025-01-01")
    assert r["spend_inr"] == 2000 and r["unique_visitors"] == 200
    assert r["bounce_rate"] == pytest.approx(0.3)
    assert r["ctr"] == pytest.approx(0.025)
    assert r["cpc_inr"] == pytest.approx(4.0)
    assert r["cpl_inr"] == pytest.approx(40.0)
    assert r["qualified_rate"] == pytest.approx(0.6)
    assert r["visitor_to_lead"] == pytest.approx(0.25)
    assert r["lead_to_customer"] == pytest.approx(0.2)


def test_customer_flows_cac_churn(monthly: pl.DataFrame) -> None:
    jan = _get(monthly, "R", "D2C", "2025-01-01")
    assert (jan["customers_at_start"], jan["new_customers"], jan["customers_lost"]) == (2, 2, 1)
    assert jan["active_customers"] == 4
    assert jan["churn_rate"] == pytest.approx(0.5)
    assert jan["cac_inr"] == pytest.approx(1000.0)
    assert jan["cac_paid_inr"] == pytest.approx(2000.0)
    assert jan["organic_share"] == pytest.approx(0.5)
    feb = _get(monthly, "R", "D2C", "2025-02-01")
    assert (feb["customers_at_start"], feb["new_customers"], feb["customers_lost"]) == (2, 1, 0)
    assert feb["churn_rate"] == 0


def test_revenue_ltv_roi(monthly: pl.DataFrame) -> None:
    jan = _get(monthly, "R", "D2C", "2025-01-01")
    assert jan["n_orders"] == 3  # the stockout-cancelled order was not served
    assert jan["revenue_inr"] == pytest.approx(1200.0)
    assert jan["gross_margin_inr"] == pytest.approx(800.0)
    assert jan["gross_margin_pct"] == pytest.approx(2 / 3)
    assert jan["aov_inr"] == pytest.approx(400.0)
    assert jan["purchase_frequency"] == pytest.approx(0.75)
    assert jan["ltv_inr"] == pytest.approx(400.0)  # 400 × 0.75 × 2/3 × 1/0.5
    assert jan["ltv_cac"] == pytest.approx(0.4)
    assert jan["roi"] == pytest.approx((5000 * 2 / 3 - 2000) / 2000)
    assert jan["mrr_inr"] == pytest.approx(600.0)  # subscriber c2's revenue


def test_zero_churn_gives_null_ltv_not_inf(monthly: pl.DataFrame) -> None:
    feb = _get(monthly, "R", "D2C", "2025-02-01")
    assert feb["ltv_inr"] is None and feb["ltv_cac"] is None


def test_nps(monthly: pl.DataFrame) -> None:
    assert _get(monthly, "R", "D2C", "2025-01-01")["nps"] == pytest.approx(25.0)
    assert _get(monthly, "R", "D2C", "2025-02-01")["nps"] is None


def test_b2b_metrics(monthly: pl.DataFrame) -> None:
    jan = _get(monthly, "R", "B2B", "2025-01-01")
    assert jan["mrr_inr"] == pytest.approx(25000.0)
    tenure = (D("2025-10-31") - D("2024-11-01")).days / 30.4375
    assert jan["ltv_inr"] == pytest.approx(100 * (250 - 100) * tenure)
    feb = _get(monthly, "R", "B2B", "2025-02-01")
    assert (feb["new_customers"], feb["customers_at_start"], feb["active_customers"]) == (1, 1, 2)
    assert feb["cac_inr"] == pytest.approx(500.0)
    assert feb["mrr_inr"] == pytest.approx(25000.0 + 50 * 200)


def test_empty_region_all_null_no_inf(monthly: pl.DataFrame) -> None:
    ratio_cols = [
        "bounce_rate", "ctr", "cpc_inr", "cpl_inr", "qualified_rate", "visitor_to_lead",
        "lead_to_customer", "cac_inr", "cac_paid_inr", "organic_share", "churn_rate", "aov_inr",
        "purchase_frequency", "gross_margin_pct", "ltv_inr", "nps", "roi", "ltv_cac",
    ]  # fmt: skip
    empty = monthly.filter(pl.col("region_id") == "R2")
    for c in ratio_cols:
        assert empty[c].null_count() == empty.height, c
    floats = monthly.select(pl.col(pl.Float64))
    for c in floats.columns:
        assert not floats[c].is_infinite().any() and not floats[c].is_nan().any(), c


def test_weekly_grain(world: dict[str, pl.DataFrame]) -> None:
    weekly = funnel_metrics(world, D("2025-01-06"), D("2025-02-24"), "week")
    assert weekly["period_start"].dt.weekday().unique().to_list() == [1]
    assert weekly["mrr_inr"].null_count() == weekly.height  # MRR is monthly by definition
    d2c = weekly.filter((pl.col("region_id") == "R") & (pl.col("channel") == "D2C"))
    assert d2c["spend_inr"].sum() == 1000  # the 2025-01-05 row falls before the window


def test_cohort_ltv(world: dict[str, pl.DataFrame]) -> None:
    table = cohort_table(world, D("2025-02-01"))
    ltv = cohort_ltv(table, LtvConfig(window_months=3))
    by = {r["cohort_month"]: r for r in ltv.iter_rows(named=True)}
    dec = by[D("2024-12-01")]
    assert dec["realized_ltv_inr"] == pytest.approx(200.0)
    assert dec["monthly_margin_per_alive_inr"] == pytest.approx(80.0)
    assert dec["monthly_churn"] == pytest.approx(0.2)
    assert dec["projected_ltv_inr"] == pytest.approx(400.0)
    jan = by[D("2025-01-01")]
    assert jan["projected_ltv_inr"] == pytest.approx(400.0)
    feb = by[D("2025-02-01")]  # zero observed churn → capped lifetime
    assert feb["projected_ltv_inr"] == pytest.approx(400 + 400 * 36)
    reg = region_ltv(ltv, LtvConfig(min_cohort_age_months=1))
    assert reg.row(0, named=True)["ltv_inr"] == pytest.approx(400.0)
    assert region_ltv(ltv).is_empty()  # default min age 3 excludes all


def test_tiny_world_runs(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Smoke only: code paths run on the fixture (no values asserted; not evaluation)."""
    m = funnel_metrics(tiny_tables, tiny_window[0], tiny_window[1], "month")
    w = funnel_metrics(tiny_tables, tiny_window[0], tiny_window[1], "week")
    assert m.height > 0 and w.height == 2 * 2 * 110
    assert not cohort_ltv(cohort_table(tiny_tables, D("2026-01-01"))).is_empty()


def test_ltv_config_from_scoring_yaml() -> None:
    from dce.config import load_scoring_config

    cfg = LtvConfig.from_scoring(load_scoring_config())
    assert cfg.max_lifetime_months == 36 and cfg.window_months == 3

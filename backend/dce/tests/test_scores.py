"""T1.4: RES (sustained lift, shrinkage, breakdown) and AQS (priors, concentration)."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from dce.config import load_scoring_config
from dce.metrics.aqs import AqsConfig
from dce.metrics.res import (
    COMPONENTS,
    ResConfig,
    StepUpConfig,
    response_evidence,
    step_up_events,
    sustained_lift,
)
from dce.metrics.scores import evidence_scores, last_complete_month

CFG = StepUpConfig()


def _step(n: int = 40, t0: int = 10, lo: float = 100.0, hi: float = 200.0) -> np.ndarray:
    s = np.full(n, lo)
    s[t0:] = hi
    return s


def test_sustained_response_scores_near_one() -> None:
    spend = _step()
    demand = np.full(40, 50.0)
    demand[11:] += 20.0  # lift from week +1 onward, never decays
    value, events = sustained_lift(spend, demand, CFG)
    assert [e.week_index for e in events] == [10]
    assert value == pytest.approx(1.0)


def test_spike_that_dies_scores_near_zero() -> None:
    spend = _step()
    demand = np.full(40, 50.0)
    demand[11:15] += 20.0  # weeks +1..+4 only
    value, _ = sustained_lift(spend, demand, CFG)
    assert value == pytest.approx(0.0)


def test_no_step_no_event_and_noise_ignored() -> None:
    rng = np.random.default_rng(0)
    noisy = 100 * rng.uniform(0.9, 1.1, 60)
    assert step_up_events(noisy, CFG) == []
    blip = np.full(40, 100.0)
    blip[10] = 200.0  # one week only: not sustained
    assert step_up_events(blip, CFG) == []
    value, events = sustained_lift(np.full(40, 100.0), np.full(40, 5.0), CFG)
    assert value is None and events == []


def test_no_demand_response_is_uninformative() -> None:
    value, events = sustained_lift(_step(), np.full(40, 50.0), CFG)
    assert len(events) == 1 and events[0].ratio is None and value is None


def _funnel_row(
    region: str, ch: str, new: int, prom: int, det: int, resp: int
) -> dict[str, object]:
    return {
        "region_id": region, "channel": ch, "period_start": date(2025, 1, 1),
        "spend_inr": 1000.0 * new, "new_customers": new, "customers_lost": new // 10,
        "customers_at_start": new, "promoters": prom, "detractors": det,
        "nps_responses": resp, "ltv_inr": None, "active_customers": new,
    }  # fmt: skip


def test_low_n_region_is_shrunk_toward_mean() -> None:
    regions = pl.DataFrame({"region_id": ["A", "B", "C"]})
    fm = pl.DataFrame(
        [
            _funnel_row("A", "D2C", 1000, 60, 20, 100),
            _funnel_row("B", "D2C", 1000, 30, 40, 100),
            _funnel_row("C", "D2C", 5, 5, 0, 5),  # looks great, but tiny sample
        ]
        + [_funnel_row(r, "B2B", 0, 0, 0, 0) for r in "ABC"],
        schema_overrides={"ltv_inr": pl.Float64},
    )
    empty_demand = pl.DataFrame(
        schema={
            "region_id": pl.String,
            "channel": pl.String,
            "week_start": pl.Date,
            "demand_kg": pl.Float64,
        }
    )
    empty_mkt = pl.DataFrame(
        schema={
            "date": pl.Date,
            "region_id": pl.String,
            "channel": pl.String,
            "spend_inr": pl.Float64,
        }
    )
    empty_orders = pl.DataFrame(
        schema={
            "channel": pl.String, "fulfilled_qty_kg": pl.Float64, "order_date": pl.Date,
            "region_id": pl.String, "customer_id": pl.String,
        }
    )  # fmt: skip
    res = response_evidence(
        regions=regions,
        demand=empty_demand,
        marketing_daily=empty_mkt,
        funnel_monthly=fm,
        region_ltv=pl.DataFrame(schema={"region_id": pl.String, "ltv_inr": pl.Float64}),
        orders=empty_orders,
        last_week=date(2025, 1, 27),
    ).filter(pl.col("channel") == "D2C")
    by = {r["region_id"]: r for r in res.iter_rows(named=True)}
    mean_raw = float(np.mean([r["res_raw"] for r in by.values()]))
    c, a = by["C"], by["A"]
    assert c["res_raw"] > a["res_raw"]  # C has the best raw NPS
    assert abs(c["res"] - mean_raw) < 0.1 * abs(c["res_raw"] - mean_raw)  # pulled hard to mean
    assert c["confidence"] == pytest.approx(5 / 105)
    assert a["confidence"] == pytest.approx(1000 / 1100)
    assert abs(a["res"] - a["res_raw"]) < 0.1 * abs(a["res_raw"] - mean_raw)  # barely moved
    assert set(c["missing_components"]) == {"lift", "econ"}
    for comp in COMPONENTS:
        assert f"z_{comp}" in res.columns and comp in res.columns


def test_evidence_scores_on_tiny_world(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code paths only; no business values asserted on fixture data."""
    scores = evidence_scores(tiny_tables, tiny_window, load_scoring_config())
    res = scores.res
    assert res.height == 4  # 2 regions × 2 channels
    assert res["res"].is_not_null().all() and res["confidence"].is_between(0, 1).all()
    rn = res.filter((pl.col("region_id") == "R_N") & (pl.col("channel") == "D2C")).row(
        0, named=True
    )
    assert rn["n_lift_events"] >= 1  # the fixture has a spend step-up in R_N

    aqs = scores.aqs
    assert aqs.height == 3
    qsr = aqs.filter(pl.col("account_id") == "ACC_QSR").row(0, named=True)
    assert qsr["prior"] is True and qsr["confidence"] == 0.0
    assert set(qsr["prior_components"]) == {"stability", "reliability", "margin", "penalty"}
    active = aqs.filter(~pl.col("prior"))
    assert (~active["prior"]).all() and (active["confidence"] > 0).all()
    assert active["stability"].is_not_null().all() and active["reliability"].is_not_null().all()


def test_concentration_flag(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    tables = dict(tiny_tables)
    tables["b2b_accounts"] = tables["b2b_accounts"].with_columns(
        pl.when(pl.col("account_id") == "ACC_QSR")
        .then(pl.lit(5000.0))
        .otherwise(pl.col("requested_kg_per_month"))
        .alias("requested_kg_per_month")
    )
    base = evidence_scores(tiny_tables, tiny_window, load_scoring_config()).aqs
    huge = evidence_scores(tables, tiny_window, load_scoring_config()).aqs

    def get(df: pl.DataFrame) -> dict[str, object]:
        return df.filter(pl.col("account_id") == "ACC_QSR").row(0, named=True)

    assert get(huge)["exceeds_concentration_cap"] is True
    assert get(huge)["aqs"] < get(base)["aqs"]  # type: ignore[operator]


def test_profiles_change_ranking_inputs_not_components(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    scoring = load_scoring_config()
    a = evidence_scores(tiny_tables, tiny_window, scoring, "reach_heavy").aqs.sort("account_id")
    b = evidence_scores(tiny_tables, tiny_window, scoring, "reliability_heavy").aqs.sort(
        "account_id"
    )
    assert a["z_reach"].to_list() == b["z_reach"].to_list()
    assert a["aqs"].to_list() != b["aqs"].to_list()


def test_configs_load_from_yaml() -> None:
    scoring = load_scoring_config()
    r = ResConfig.from_scoring(scoring)
    assert sum(r.weights.values()) == pytest.approx(1.0)
    assert r.step_up.late_weeks == (5, 10)
    a = AqsConfig.from_scoring(scoring)
    assert {"balanced", "reach_heavy", "reliability_heavy"} <= set(a.profiles)


@pytest.mark.parametrize(
    ("last_week", "expected"),
    [
        (date(2026, 2, 2), date(2026, 1, 1)),  # ends Sun 2026-02-08: Feb incomplete
        (date(2025, 3, 24), date(2025, 2, 1)),  # ends 2025-03-30: March incomplete
        (date(2025, 8, 25), date(2025, 8, 1)),  # ends 2025-08-31: August complete
        (date(2025, 1, 6), date(2024, 12, 1)),  # year boundary
    ],
)
def test_last_complete_month(last_week: date, expected: date) -> None:
    assert last_complete_month(last_week) == expected

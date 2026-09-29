"""T6.1: weekly breach + surplus detection from sample paths."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.risk.detect import detect, weekly_supply_paths

H = 6
HORIZON = [date(2025, 1, 6) + timedelta(weeks=i) for i in range(H)]


def test_breach_probability_and_expected_shortfall_by_hand() -> None:
    # 4 paths; week 3 short on 2 of 4 paths by 10 and 30 kg
    demand = np.full((4, H), 100.0)
    supply = np.full((4, H), 120.0)
    supply[0, 2], supply[1, 2] = 90.0, 70.0
    r = detect(demand, supply, HORIZON, breach_threshold=0.5, surplus_probability=1.1)
    w = r.weekly.row(2, named=True)
    assert w["p_breach"] == 0.5
    assert w["expected_shortfall_kg"] == pytest.approx((10 + 30) / 4)
    assert r.breach_week == HORIZON[2]
    a = r.first("breach")
    assert a is not None and a.weeks_until == 2 and a.start == a.end == HORIZON[2]


def test_mode_threshold_decides_flagging() -> None:
    gen = np.random.default_rng(0)
    demand = gen.normal(100, 10, (2000, H))
    supply = np.full((2000, H), 110.0)  # P(breach) ≈ 16% every week
    stability = detect(demand, supply, HORIZON, breach_threshold=0.15)
    growth = detect(demand, supply, HORIZON, breach_threshold=0.30)
    assert stability.breach_week == HORIZON[0]
    assert growth.breach_week is None


def test_consecutive_weeks_form_one_alert() -> None:
    demand = np.full((10, H), 100.0)
    supply = np.full((10, H), 150.0)
    supply[:, 1:4] = 80.0  # weeks 2–4 short on every path
    r = detect(demand, supply, HORIZON, breach_threshold=0.3)
    breaches = [a for a in r.alerts if a.kind == "breach"]
    assert len(breaches) == 1
    a = breaches[0]
    assert (a.start, a.end, a.peak_probability) == (HORIZON[1], HORIZON[3], 1.0)
    assert a.expected_kg == pytest.approx(3 * 20)


def test_surplus_flagged_and_never_in_a_breach_week() -> None:
    demand = np.full((10, H), 100.0)
    supply = np.full((10, H), 105.0)
    supply[:, 4:] = 200.0  # large surplus in weeks 5–6
    r = detect(demand, supply, HORIZON, breach_threshold=0.3, surplus_share=0.15)
    assert r.surplus_week == HORIZON[4]
    assert r.breach_week is None
    both = r.weekly.filter(pl.col("breach") & pl.col("surplus"))
    assert both.is_empty()
    assert r.first("surplus").expected_kg == pytest.approx(2 * 100)  # type: ignore[union-attr]


def test_shape_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        detect(np.zeros((3, H)), np.zeros((3, H - 1)), HORIZON, 0.3)


def test_fixture_pipeline_risk_and_coman_lead_time(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code paths on the fixture (not evaluation): risk report + committed co-man supply."""
    from dce.runner import build_run_config, run_pipeline

    run = build_run_config("STABILITY", seed=3)
    run = type(run)(
        run.mode,
        run.mode_config,
        run.app
        | {
            "n_paths": 60,
            "forecast": {
                **run.app["forecast"],
                "models": ["seasonal_naive_52", "window_average_8"],
            },
        },
        run.scoring,
        run.seed,
    )
    out = run_pipeline(tiny_tables, tiny_window, run)
    wk = out.risk.weekly
    assert wk.height == len(out.forecast.horizon)
    assert wk["p_breach"].is_between(0, 1).all() and wk["p_surplus"].is_between(0, 1).all()
    assert out.risk.breach_threshold == out.mode.breach_threshold

    # Committed co-man only adds supply from the partner's earliest output week.
    partners = out.coman.partners
    assert partners
    horizon = out.forecast.horizon
    q = np.zeros((len(partners), out.inputs.M))
    q[0, :] = 1000.0
    args = (out.inputs, out.capacity, horizon, partners)
    base = weekly_supply_paths(*args, np.zeros_like(q), horizon[0], 60, 3)
    with_cm = weekly_supply_paths(*args, q, horizon[0], 60, 3)
    added = (with_cm - base).mean(axis=0)
    mask = partners[0].output_mask(horizon, horizon[0])
    assert (added[~mask] == 0).all()
    assert (added[mask] > 0).all()

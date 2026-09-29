"""T5.7: Monte Carlo stress test of a fixed plan."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from dce.optimize.stress import PlanDecision, Scenarios, stress_test
from dce.tests.test_optimize_lp import inputs

P = 400


def scen(d2c: list[float], b2b: list[float], cap: np.ndarray | float, M: int = 1) -> Scenarios:
    return Scenarios(
        d2c=np.array([[[v] * M] * P for v in d2c], float).reshape(len(d2c), P, M),
        b2b=np.array([[[v] * M] * P for v in b2b], float).reshape(len(b2b), P, M),
        inhouse=np.broadcast_to(
            np.asarray(cap, float).reshape(-1, 1) if np.ndim(cap) else np.full((P, M), cap), (P, M)
        ).copy(),
        coman_reliability=np.zeros((0, P, M)),
    )


def decision(x: list[float], y: list[float]) -> PlanDecision:
    return PlanDecision(
        "t",
        np.array(x, float).reshape(len(x), 1),
        np.array(y, float).reshape(len(y), 1),
        np.zeros((0, 1)),
        np.zeros(0),
        np.zeros((len(x), 1)),
        0.0,
    )


W = (1.0, 1.0, 0.3)


def test_ample_supply_serves_everything() -> None:
    inp = inputs([1000], [[300]], [[200]], carry=100)
    r = stress_test(decision([300], [200]), scen([300], [200], 1000.0), inp, W, 0.85, 400.0)
    s = r.summary()
    assert s["d2c_fill_rate"]["mean"] == 1.0 and s["b2b_fill_rate"]["mean"] == 1.0
    assert s["waste_kg"]["mean"] == pytest.approx(1000 - 500 - 100)
    assert s["revenue_inr"]["mean"] == pytest.approx(300 * 1500 + 200 * 1200)
    assert s["any_b2b_shortfall"]["mean"] == 0.0


def test_never_serves_more_than_supply_or_demand() -> None:
    rng = np.random.default_rng(0)
    inp = inputs([800], [[300], [200]], [[400]])
    sc = scen([300, 200], [400], 0.0)
    sc.inhouse = rng.uniform(200, 1200, (P, 1))
    sc.d2c = rng.uniform(100, 400, (2, P, 1))
    r = stress_test(decision([300, 200], [400]), sc, inp, W, 0.9, 400.0)
    assert (r.metrics["served_kg"] <= sc.inhouse[:, 0] + 1e-9).all()
    assert (r.metrics["served_kg"] <= sc.d2c.sum(axis=(0, 2)) + 400 + 1e-9).all()


def test_shortage_serves_b2b_floor_first_then_by_value() -> None:
    inp = inputs([300], [[300]], [[200]])
    # plan gives D2C everything, B2B nothing; floors still come first in execution
    r = stress_test(decision([300], [200]), scen([300], [200], 100.0), inp, W, 0.9, 400.0)
    s = r.summary()
    assert s["b2b_fill_rate"]["mean"] == pytest.approx(0.5)  # 90% floor of 200 = 180 > 100 supply
    assert s["d2c_fill_rate"]["mean"] == 0.0
    assert s["any_b2b_shortfall"]["mean"] == 1.0


def test_entitlements_matter_under_shortage() -> None:
    inp = inputs([400], [[300], [300]], [])
    sc = scen([300, 300], [], 400.0)
    a = stress_test(decision([300, 100], []), sc, inp, W, 0.9, 400.0)
    b = stress_test(decision([100, 300], []), sc, inp, W, 0.9, 400.0)
    assert a.summary()["served_kg"]["mean"] == b.summary()["served_kg"]["mean"] == 400
    # identical economics here, so check the split directly via a priority tie-break-free setup
    inp2 = inputs([400], [[300], [300]], [])
    inp2.p_d2c = np.array([1500.0, 2000.0])  # R1 more valuable, but R0 holds the entitlement
    c = stress_test(decision([300, 100], []), scen([300, 300], [], 400.0), inp2, W, 0.9, 400.0)
    rev_c = c.summary()["revenue_inr"]["mean"]
    assert rev_c == pytest.approx(300 * 1500 + 100 * 2000)  # entitlements honored before value


def test_shortfall_probability_rises_as_capacity_tightens() -> None:
    inp = inputs([1000], [[400]], [[400]])
    rng = np.random.default_rng(1)
    probs = []
    for level in (1200, 900, 700):
        sc = scen([400], [400], 0.0)
        sc.inhouse = rng.normal(level, 150, (P, 1)).clip(0)
        probs.append(
            stress_test(decision([400], [400]), sc, inp, W, 0.98, 400.0).summary()[
                "any_b2b_shortfall"
            ]["mean"]
        )
    assert probs[0] < probs[1] < probs[2]


def test_fixture_plan_stress(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code paths on the fixture: pipeline plan → scenarios → stress summary."""
    from dce.optimize.explain import explain
    from dce.optimize.stress import build_scenarios
    from dce.runner import build_run_config, run_pipeline

    run = build_run_config("STABILITY", seed=2)
    run = type(run)(
        run.mode,
        run.mode_config,
        run.app
        | {
            "n_paths": 80,
            "forecast": {
                **run.app["forecast"],
                "models": ["seasonal_naive_52", "window_average_8"],
            },
        },
        run.scoring,
        run.seed,
    )
    out = run_pipeline(tiny_tables, tiny_window, run)
    sc = build_scenarios(out.inputs, out.forecast, out.capacity, out.coman.partners, seed=2)
    assert sc.d2c.shape == (2, 80, 3) and sc.inhouse.shape == (80, 3)
    dec = PlanDecision.from_plan(out.plan, out.coman.partners, explain(out.plan).duals)
    m = out.mode
    res = stress_test(dec, sc, out.inputs, (m.w_rev, m.w_pen, m.w_gw), m.b2b_service_floor, 900.0)
    s = res.summary_frame()
    assert set(s["metric"]) >= {"revenue_inr", "b2b_fill_rate", "any_b2b_shortfall", "waste_kg"}
    assert 0 <= res.summary()["any_b2b_shortfall"]["mean"] <= 1

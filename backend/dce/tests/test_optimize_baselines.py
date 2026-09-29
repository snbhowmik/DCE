"""T5.8: rule baselines (proportional, B2B-first, FCFS) through the same stress test."""

from __future__ import annotations

import numpy as np
import pytest

from dce.optimize.baselines import baseline_decision, compare_with_baselines, water_fill
from dce.optimize.plan import solve_plan
from dce.optimize.stress import PlanDecision, stress_test
from dce.tests.test_optimize_lp import STABILITY, inputs
from dce.tests.test_optimize_stress import P, scen

W = (STABILITY.w_rev, STABILITY.w_pen, STABILITY.w_gw)


def test_water_fill() -> None:
    assert water_fill(100, np.array([50.0, 50.0])).tolist() == [50, 50]
    assert water_fill(60, np.array([30.0, 90.0])).tolist() == pytest.approx([15, 45])
    # proportional to demand (not max-min fair): the small line gets its proportional share
    assert water_fill(100, np.array([10.0, 200.0, 200.0])).tolist() == pytest.approx(
        [100 * 10 / 410, 100 * 200 / 410, 100 * 200 / 410]
    )
    # capacity above total demand: everyone fully served, never above demand
    assert water_fill(1000, np.array([10.0, 200.0])).tolist() == pytest.approx([10, 200])
    assert water_fill(0, np.array([5.0])).tolist() == [0]


def test_baseline_plans_respect_capacity_and_demand() -> None:
    inp = inputs([500] * 3, [[300] * 3, [100] * 3], [[400] * 3])
    for name in ("proportional", "b2b_first"):
        d = baseline_decision(name, inp, 0.0)
        assert (d.x <= inp.d2c_demand + 1e-9).all() and (d.y <= inp.commit + 1e-9).all()
        assert (d.x.sum(0) + d.y.sum(0) <= inp.cap_in + 1e-6).all()
    b = baseline_decision("b2b_first", inp, 0.0)
    assert b.y[0, 0] == pytest.approx(400) and b.x[:, 0].sum() == pytest.approx(100)
    p = baseline_decision("proportional", inp, 0.0)
    assert p.y[0, 0] == pytest.approx(500 * 400 / 800)
    f = baseline_decision("fcfs", inp, 0.0)
    assert f.x.sum() == 0 and f.y.sum() == 0


def test_fcfs_is_pro_rata_on_realized_orders() -> None:
    inp = inputs([300], [[300]], [[300]])
    d = baseline_decision("fcfs", inp, 0.0)
    r = stress_test(d, scen([300], [300], 300.0), inp, W, 0.0, 400.0, rationing="pro_rata")
    s = r.summary()
    assert s["d2c_fill_rate"]["mean"] == pytest.approx(0.5)
    assert s["b2b_fill_rate"]["mean"] == pytest.approx(0.5)


def test_comparison_same_scenarios_all_plans() -> None:
    inp = inputs([500], [[300]], [[400]])
    plan = solve_plan(inp, STABILITY)
    sc = scen([300], [400], 500.0)
    opt = stress_test(PlanDecision.from_plan(plan, []), sc, inp, W, 0.98, 400.0)
    cmp_ = compare_with_baselines(opt, inp, sc, W, 0.98, 400.0, 0.0)
    means = cmp_.means()
    assert set(means["plan"]) == {"STABILITY", "proportional", "b2b_first", "fcfs"}
    by = {r["plan"]: r for r in means.iter_rows(named=True)}
    # every plan faces the same supply, so total served kg is equal here (supply-bound)
    assert len({round(r["served_kg"], 6) for r in by.values()}) == 1
    assert by["STABILITY"]["b2b_fill_rate"] >= by["b2b_first"]["b2b_fill_rate"] - 1e-9
    assert by["STABILITY"]["b2b_fill_rate"] > by["proportional"]["b2b_fill_rate"]
    assert P > 0

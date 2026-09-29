"""T5.3: spend co-optimization (response segments, lag, budget, evidence gate, exploration)."""

from __future__ import annotations

import numpy as np
import polars as pl

from dce.optimize.inputs import ModeParams
from dce.optimize.plan import solve_plan
from dce.optimize.spend import SpendInputs, monthly_lag_matrix
from dce.response.pwl import ResponseCurve
from dce.tests.test_optimize_lp import inputs, months

WK = np.array([4.0, 4.0, 4.0])


def curve(region: str, slope: float, cap_week: float = 20000.0, low: bool = False) -> ResponseCurve:
    """Concave 2-segment curve: first half at `slope`, second half at slope/4 (kg per ₹)."""
    bp = np.array([0.0, cap_week / 2, cap_week])
    return ResponseCurve(region, bp, np.diff(bp), np.array([slope, slope / 4]), cap_week, low, 0.0)


def spend_inputs(curves: list[ResponseCurve], planned: list[list[float]], res: list[float],
                 theta: float = 0.0) -> SpendInputs:  # fmt: skip
    regs = [c.region_id for c in curves]
    p = np.array(planned, float)
    return SpendInputs(
        curves={c.region_id: c for c in curves},
        theta=dict.fromkeys(regs, theta),
        planned=p,
        res=dict(zip(regs, res, strict=True)),
        ltv_cac={},
        budget=p.sum(axis=0),
    )


MODE = ModeParams("GROWTH", w_gw=0.3, b2b_service_floor=0.85, res_gate=0.0, exploration_share=0.10)


def test_lag_matrix() -> None:
    ms = months(3)
    assert np.allclose(monthly_lag_matrix(0.0, ms), np.eye(3))
    L = monthly_lag_matrix(0.6, ms)
    assert np.allclose(np.triu(L, 1), 0)  # no effect before spending
    assert (L.sum(axis=0) <= 1 + 1e-12).all() and L[1, 0] > 0
    assert L.sum(axis=0)[0] > L.sum(axis=0)[2]  # late spend loses more beyond the horizon


def test_spend_at_plan_reproduces_forecast() -> None:
    inp = inputs([1e5] * 3, [[1000] * 3, [1000] * 3], [])
    low = [curve("R0", 0.002, low=True), curve("R1", 0.002, low=True)]
    sin = spend_inputs(low, [[40000] * 3, [40000] * 3], [1.0, 1.0], theta=0.5)
    res = solve_plan(inp, ModeParams("X", exploration_share=0.0), sin)
    assert res.optimal
    sp = res.extras["spend"]
    assert np.allclose(sp["recommended_inr"], sp["planned_inr"])
    assert np.allclose(res.extras["d2c_demand_effective"], inp.d2c_demand)


def test_budget_moves_to_better_region_when_capacity_allows() -> None:
    inp = inputs([1e6] * 3, [[1000] * 3, [1000] * 3], [])
    # R0: 0.002 kg/₹ × ₹1500/kg = ₹3 value per ₹ (profitable); R1: 0.0002 → ₹0.3 (not)
    sin = spend_inputs([curve("R0", 0.002), curve("R1", 0.0002)], [[40000] * 3] * 2, [1.0, 1.0])
    res = solve_plan(inp, MODE, sin)
    sp = res.extras["spend"]
    r0 = sp.filter(sp["region_id"] == "R0")["recommended_inr"].to_numpy()
    r1 = sp.filter(sp["region_id"] == "R1")["recommended_inr"].to_numpy()
    assert (r0 > 40000).all() and (r1 < 40000).all()
    assert np.allclose(r0 + r1, 80000)  # budget binding
    assert (res.extras["d2c_demand_effective"][0] > inp.d2c_demand[0]).all()


def test_budget_never_exceeded() -> None:
    inp = inputs([1e6] * 3, [[1000] * 3, [1000] * 3], [])
    sin = spend_inputs([curve("R0", 0.01), curve("R1", 0.01)], [[40000] * 3] * 2, [1.0, 1.0])
    res = solve_plan(inp, MODE, sin)
    sp = res.extras["spend"]
    per_month = sp.group_by("month").agg(pl.col("recommended_inr").sum()).sort("month")
    assert (per_month["recommended_inr"].to_numpy() <= sin.budget + 1e-6).all()


def test_evidence_gate_limits_expansion_to_exploration_pool() -> None:
    inp = inputs([1e6] * 3, [[1000] * 3, [1000] * 3], [])
    # R0 is the attractive one but fails the RES gate; R1 passes but is unattractive
    sin = spend_inputs([curve("R0", 0.002), curve("R1", 0.0002)], [[40000] * 3] * 2, [-1.0, 1.0])
    res = solve_plan(inp, MODE, sin)
    sp = res.extras["spend"]
    extra_r0 = sp.filter(sp["region_id"] == "R0")["delta_inr"].to_numpy()
    assert (extra_r0 <= 0.10 * sin.budget + 1e-6).all() and (extra_r0 > 0).all()
    assert sp.filter(sp["region_id"] == "R0")["gated"].all()


def test_low_confidence_held_at_plan() -> None:
    inp = inputs([1e6] * 3, [[1000] * 3, [1000] * 3], [])
    sin = spend_inputs(
        [curve("R0", 0.0001, low=True), curve("R1", 0.002)], [[40000] * 3] * 2, [1.0, 1.0]
    )
    res = solve_plan(inp, ModeParams("X", exploration_share=0.0), sin)
    r0 = res.extras["spend"].filter(res.extras["spend"]["region_id"] == "R0")
    assert np.allclose(r0["recommended_inr"], 40000)  # would be cut if it were a free lever


def test_no_expansion_when_capacity_is_short() -> None:
    """Spend creates demand that must be served: with no spare capacity, spend is cut."""
    inp = inputs([1500] * 3, [[1000] * 3, [1000] * 3], [])  # 2000 kg demand vs 1500 capacity
    sin = spend_inputs([curve("R0", 0.002), curve("R1", 0.002)], [[40000] * 3] * 2, [1.0, 1.0])
    res = solve_plan(inp, MODE, sin)
    sp = res.extras["spend"]
    assert (sp["recommended_inr"] <= sp["planned_inr"] + 1e-6).all()
    assert sp["recommended_inr"].sum() < sp["planned_inr"].sum()
    for m in range(3):
        assert res.x[:, m].sum() <= 1500 + 1e-6


def test_held_spend_cannot_game_segment_order_when_capacity_short() -> None:
    """Regression: with spend held at plan and extra D2C demand undesirable (capacity short),
    the LP relaxation booked spend into flat segments to 'reduce' demand. Exact PWL forbids it."""
    inp = inputs([1200] * 3, [[1000] * 3, [1000] * 3], [])
    held = [curve("R0", 0.002, low=True), curve("R1", 0.002, low=True)]
    sin = spend_inputs(held, [[60000] * 3] * 2, [1.0, 1.0], theta=0.4)
    res = solve_plan(inp, ModeParams("X", w_gw=0.6, exploration_share=0.0), sin)
    assert res.optimal
    assert np.allclose(res.extras["d2c_demand_effective"], inp.d2c_demand, atol=1e-4)

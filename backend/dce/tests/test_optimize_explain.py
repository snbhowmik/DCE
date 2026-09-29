"""T5.6: shadow prices (incl. LP relaxation of the MILP), binding constraints, line drivers."""

from __future__ import annotations

import pulp
import pytest

from dce.optimize.explain import Explanation, explain
from dce.optimize.plan import solve_plan
from dce.tests.test_optimize_lp import GROWTH, inputs
from dce.tests.test_optimize_spend import MODE, curve, spend_inputs


def codes(ex: Explanation, channel: str, line: str, month: int = 0) -> list[str]:
    row = ex.drivers.filter(
        (ex.drivers["channel"] == channel)
        & (ex.drivers["line"] == line)
        & (ex.drivers["month"] == month)
    ).row(0, named=True)
    return [r["code"] for r in row["reasons"]]


def test_shortage_shadow_price_and_drivers() -> None:
    res = solve_plan(inputs([300], [[300]], [[200]]), GROWTH)
    ex = explain(res)
    # D2C is the marginal line: +1 kg capacity is worth its price + goodwill weight × goodwill
    assert ex.duals["capacity_m0"] == pytest.approx(1500 + 0.3 * 375)
    assert "capacity" in set(ex.binding["kind"])
    assert codes(ex, "D2C", "R0") == ["capacity_bound"]
    assert codes(ex, "B2B", "A0") == ["at_service_floor"]


def test_surplus_capacity_is_worth_minus_waste_cost() -> None:
    res = solve_plan(inputs([1000], [[300]], [[200]]), GROWTH)
    ex = explain(res)
    assert ex.duals["capacity_m0"] == pytest.approx(-900.0)
    assert codes(ex, "D2C", "R0") == ["fully_served"] and codes(ex, "B2B", "A0") == ["fully_served"]


def test_milp_duals_via_relaxation_and_spend_drivers() -> None:
    inp = inputs([1e6] * 3, [[1000] * 3, [1000] * 3], [])
    # R0's cap (₹40k/week) exceeds the whole budget, so the budget binds
    sin = spend_inputs(
        [curve("R0", 0.002, cap_week=40000.0), curve("R1", 0.0002)], [[40000] * 3] * 2, [1.0, 1.0]
    )
    res = solve_plan(inp, MODE, sin)
    assert res.duals == {}  # MILP: no direct duals
    ex = explain(res)
    # The budget (₹80k) exactly fills R0's first segment (₹20k/week × 4), so the *next* ₹1
    # lands in segment 2 (slope 0.0005): worth ₹1500 price + ₹900 waste avoided, minus the ₹1
    assert ex.duals["budget_m0"] == pytest.approx(0.0005 * 2400 - 1)
    assert "budget_binding" in codes(ex, "SPEND", "R0")
    ints = [v for v in res.extras["model"].prob.variables() if v.name.startswith("spz_")]
    assert ints and all(v.cat == pulp.LpInteger for v in ints)  # integrality restored


def test_capped_region_explained_and_budget_worth_nothing() -> None:
    inp = inputs([1e6] * 3, [[1000] * 3, [1000] * 3], [])
    sin = spend_inputs([curve("R0", 0.002), curve("R1", 0.0002)], [[40000] * 3] * 2, [1.0, 1.0])
    ex = explain(solve_plan(inp, MODE, sin))
    assert "extrapolation_cap" in codes(ex, "SPEND", "R0")
    assert ex.duals.get("budget_m0", 0.0) == pytest.approx(0.0)  # no profitable use for +₹1


def test_every_line_has_reasons() -> None:
    res = solve_plan(inputs([500] * 3, [[300] * 3, [100] * 3], [[200] * 3]), GROWTH)
    ex = explain(res)
    assert ex.drivers.height == 3 * 3
    assert all(len(r) >= 1 for r in ex.drivers["reasons"].to_list())
    for reasons in ex.drivers["reasons"].to_list():
        assert all({"code", "detail", "value"} <= set(r) for r in reasons)

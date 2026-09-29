"""T5.9: invariant + property tests on 200 random small instances (ARCH §9 items 1–3, 9).

Item 8 (zero-volume onboarding candidate = baseline) is asserted in T7.1 where the simulator lives.
"""

from __future__ import annotations

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dce.optimize.inputs import ModeParams, PlanInputs
from dce.optimize.lp import solve_allocation
from dce.optimize.stress import PlanDecision, Scenarios, stress_test
from dce.tests.test_optimize_lp import months

SETTINGS = settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow], derandomize=True
)
TOL = 1e-5


def fl(lo: float, hi: float) -> st.SearchStrategy[float]:
    """Realistic magnitudes: no NaN, no subnormals (≈1e-309 kg is not a quantity)."""
    return st.floats(lo, hi, allow_nan=False, allow_subnormal=False)


def grid(
    draw: st.DrawFn, rows: int, cols: int, strat: st.SearchStrategy, dtype: type
) -> np.ndarray:
    return np.array([[draw(strat) for _ in range(cols)] for _ in range(rows)], dtype=dtype).reshape(
        rows, cols
    )


@st.composite
def instances(draw: st.DrawFn) -> tuple[PlanInputs, ModeParams]:
    M = draw(st.integers(1, 3))
    R = draw(st.integers(1, 3))
    A = draw(st.integers(0, 3))
    cap = np.array([draw(fl(0, 3000)) for _ in range(M)])
    inp = PlanInputs(
        months=months(M),
        regions=[f"R{r}" for r in range(R)],
        accounts=[f"A{a}" for a in range(A)],
        account_region=["R0"] * A,
        cap_in=cap,
        carry_limit=cap * draw(fl(0, 0.5)),
        d2c_demand=grid(draw, R, M, fl(0, 1000), float),
        commit=grid(draw, A, M, fl(0, 1000), float),
        p_d2c=np.array([draw(fl(500, 2000)) for _ in range(R)]),
        p_b2b=np.array([draw(fl(400, 1500)) for _ in range(A)]),
        penalty=np.array([draw(fl(0, 300)) for _ in range(A)]),
        goodwill=np.array([draw(fl(0, 500)) for _ in range(R)]),
        reach=np.array([draw(fl(0, 1)) for _ in range(A)]),
        waste_cost=draw(fl(0, 1000)),
        d2c_eligible=grid(draw, R, M, st.booleans(), bool),
        b2b_eligible=grid(draw, A, M, st.booleans(), bool),
        concentration_cap=draw(fl(0.1, 1.0)),
        floor_penalty=1e6,
    )
    mode = ModeParams(
        "P",
        w_pen=draw(fl(0, 3)),
        w_gw=draw(fl(0, 1)),
        w_reach=draw(fl(0, 0.5)),
        b2b_service_floor=draw(fl(0, 1)),
    )
    return inp, mode


@SETTINGS
@given(instances())
def test_capacity_and_demand_bounds(case: tuple[PlanInputs, ModeParams]) -> None:
    """ARCH §9.1–9.2: allocation ≤ available supply; x ≤ D2C demand; y ≤ Commit; eligibility."""
    inp, mode = case
    res = solve_allocation(inp, mode)
    assert res.optimal
    for m in range(inp.M):
        prev = res.inv[m - 1] if m else inp.initial_inventory
        assert res.x[:, m].sum() + res.y[:, m].sum() <= inp.cap_in[m] + prev + TOL
    assert (res.x <= inp.d2c_demand + TOL).all() and (res.y <= inp.commit + TOL).all()
    assert (res.x[~inp.d2c_eligible] <= TOL).all() and (res.y[~inp.b2b_eligible] <= TOL).all()
    assert (res.inv <= inp.carry_limit + TOL).all()


@SETTINGS
@given(instances())
def test_floors_hold_when_feasible_else_reported(case: tuple[PlanInputs, ModeParams]) -> None:
    """ARCH §9.3: if floors are satisfiable month by month, no slack; any slack is reported."""
    inp, mode = case
    res = solve_allocation(inp, mode)
    floors = mode.b2b_service_floor * inp.commit * inp.b2b_eligible
    feasible = all(
        floors[:, m].sum() <= inp.cap_in[m] + TOL
        and (floors[:, m] <= inp.concentration_cap * inp.cap_in[m] + TOL).all()
        for m in range(inp.M)
    )
    if feasible:
        assert (res.floor_slack <= 1e-4).all()
    assert (res.floor_slack > 1e-4).sum() == res.floor_violations().height


@SETTINGS
@given(instances(), fl(0.0, 1.0))
def test_more_capacity_never_hurts(case: tuple[PlanInputs, ModeParams], extra: float) -> None:
    """ARCH §9.9, restated (D-048): objective + h·Σcap is non-decreasing in capacity.

    The waste term −h·waste = −h·cap + h·used shifts the objective by −h per kg of capacity
    without changing any decision, so the raw objective can fall when surplus is wasted.
    """
    inp, mode = case
    a = solve_allocation(inp, mode)
    bigger = inp.with_capacity(inp.cap_in * (1 + extra) + 1.0)
    b = solve_allocation(bigger, mode)
    assert a.objective is not None and b.objective is not None
    adj_a = a.objective + inp.waste_cost * inp.cap_in.sum()
    adj_b = b.objective + inp.waste_cost * bigger.cap_in.sum()
    assert adj_b >= adj_a - 1e-6 * max(1.0, abs(adj_a))


@SETTINGS
@given(instances(), st.integers(0, 2**31 - 1))
def test_stress_never_exceeds_supply_on_any_path(
    case: tuple[PlanInputs, ModeParams], seed: int
) -> None:
    """ARCH §9.1 for the stress test: served kg ≤ supply on every path and month."""
    inp, mode = case
    res = solve_allocation(inp, mode)
    P = 50
    rng = np.random.default_rng(seed)
    R, A = len(inp.regions), len(inp.accounts)
    scen = Scenarios(
        d2c=rng.uniform(0, 1000, (R, P, inp.M)),
        b2b=rng.uniform(0, 1000, (A, P, inp.M)),
        inhouse=rng.uniform(0, 3000, (P, inp.M)),
        coman_reliability=np.zeros((0, P, inp.M)),
    )
    dec = PlanDecision.from_plan(res, [])
    out = stress_test(dec, scen, inp, (1.0, mode.w_pen, mode.w_gw), mode.b2b_service_floor, 400.0)
    supply_total = scen.inhouse.sum(axis=1) + inp.initial_inventory
    assert (out.metrics["served_kg"] <= supply_total + 1e-6).all()
    demand_total = scen.d2c.sum(axis=(0, 2)) + scen.b2b.sum(axis=(0, 2))
    assert (out.metrics["served_kg"] <= demand_total + 1e-6).all()
    assert (
        (out.metrics["b2b_fill_rate"] >= -TOL) & (out.metrics["b2b_fill_rate"] <= 1 + TOL)
    ).all()

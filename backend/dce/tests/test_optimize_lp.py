"""T5.1: core allocation LP on hand-solvable instances + fixture integration."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.optimize.inputs import ModeParams, Month, PlanInputs, build_inputs, split_months
from dce.optimize.lp import solve_allocation

W0 = date(2026, 2, 9)


def months(n: int = 1) -> list[Month]:
    hz = [W0 + timedelta(weeks=i) for i in range(4 * n)]
    return split_months(hz, [4] * n)


def inputs(
    cap: list[float],
    d2c: list[list[float]],
    commit: list[list[float]],
    *,
    carry: float = 0.0,
    d2c_elig: np.ndarray | None = None,
    conc: float = 1.0,
) -> PlanInputs:
    M, R, A = len(cap), len(d2c), len(commit)
    return PlanInputs(
        months=months(M),
        regions=[f"R{r}" for r in range(R)],
        accounts=[f"A{a}" for a in range(A)],
        account_region=["R0"] * A,
        cap_in=np.array(cap, float),
        carry_limit=np.full(M, carry),
        d2c_demand=np.array(d2c, float).reshape(R, M),
        commit=np.array(commit, float).reshape(A, M),
        p_d2c=np.full(R, 1500.0),
        p_b2b=np.full(A, 1200.0),
        penalty=np.full(A, 200.0),
        goodwill=np.full(R, 375.0),
        reach=np.zeros(A),
        waste_cost=900.0,
        d2c_eligible=d2c_elig if d2c_elig is not None else np.ones((R, M), bool),
        b2b_eligible=np.ones((A, M), bool),
        concentration_cap=conc,
        floor_penalty=1e6,
    )


GROWTH = ModeParams("GROWTH", w_pen=1.0, w_gw=0.3, b2b_service_floor=0.85)
STABILITY = ModeParams("STABILITY", w_pen=3.0, w_gw=0.6, b2b_service_floor=0.98)


def test_surplus_serves_all_and_wastes_rest() -> None:
    res = solve_allocation(inputs([1000], [[300]], [[200]], carry=100), GROWTH)
    assert res.optimal
    assert res.x[0, 0] == pytest.approx(300) and res.y[0, 0] == pytest.approx(200)
    assert res.inv[0] == pytest.approx(100)  # carried rather than wasted
    assert res.waste[0] == pytest.approx(400)


def test_shortage_tradeoff_depends_on_mode() -> None:
    # D2C kg worth 1500 + gw·375; B2B kg worth 1200 + pen·200.
    g = solve_allocation(inputs([300], [[300]], [[200]]), GROWTH)
    # GROWTH: D2C 1612.5 > B2B 1400 → B2B only at its 85% floor (170), D2C gets the rest.
    assert g.y[0, 0] == pytest.approx(170) and g.x[0, 0] == pytest.approx(130)
    s = solve_allocation(inputs([300], [[300]], [[200]]), STABILITY)
    # STABILITY: B2B 1800 > D2C 1725 → B2B fully served.
    assert s.y[0, 0] == pytest.approx(200) and s.x[0, 0] == pytest.approx(100)
    assert not g.floor_violations().height and not s.floor_violations().height


def test_infeasible_floor_is_soft_and_reported() -> None:
    res = solve_allocation(inputs([100], [[50]], [[200]]), STABILITY)
    assert res.optimal
    assert res.y[0, 0] == pytest.approx(100) and res.x[0, 0] == pytest.approx(0)
    v = res.floor_violations()
    assert v.height == 1 and v["floor_slack_kg"][0] == pytest.approx(196 - 100)


def test_concentration_cap() -> None:
    res = solve_allocation(inputs([1000], [[0]], [[800]], conc=0.3), STABILITY)
    assert res.y[0, 0] == pytest.approx(300)
    assert res.floor_violations()["floor_slack_kg"][0] == pytest.approx(0.98 * 800 - 300)


def test_ineligible_region_gets_nothing() -> None:
    elig = np.array([[False], [True]])
    res = solve_allocation(inputs([1000], [[100], [100]], [], d2c_elig=elig), GROWTH)
    assert res.x[0, 0] == 0 and res.u[0, 0] == pytest.approx(100)
    assert res.x[1, 0] == pytest.approx(100)


def test_carryover_moves_surplus_forward() -> None:
    res = solve_allocation(inputs([500, 100], [[200, 300]], [], carry=150), GROWTH)
    assert res.inv[0] == pytest.approx(150)
    assert res.x[0, 1] == pytest.approx(250)  # 100 produced + 150 carried
    assert res.waste[0] == pytest.approx(150)


def test_capacity_balance_and_bounds() -> None:
    rng = np.random.default_rng(0)
    for _ in range(20):
        cap = list(rng.uniform(100, 1000, 3))
        d2c = rng.uniform(0, 400, (2, 3)).tolist()
        com = rng.uniform(0, 300, (2, 3)).tolist()
        inp = inputs(cap, d2c, com, carry=50)
        res = solve_allocation(inp, STABILITY)
        assert res.optimal
        for m in range(3):
            prev = res.inv[m - 1] if m else 0.0
            used = res.x[:, m].sum() + res.y[:, m].sum()
            assert used <= inp.cap_in[m] + prev + 1e-6
            assert used + res.inv[m] + res.waste[m] == pytest.approx(inp.cap_in[m] + prev)
        assert (res.x <= inp.d2c_demand + 1e-6).all() and (res.y <= inp.commit + 1e-6).all()


def test_fixture_end_to_end(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code paths only: forecast + capacity → inputs → optimal plan; no business claims."""
    from dce.capacity.model import capacity_forecast
    from dce.forecast.run import forecast_dataset
    from dce.runner import build_run_config

    run = build_run_config("STABILITY", seed=1)
    app = run.app | {
        "n_paths": 60,
        "forecast": {"models": ["seasonal_naive_52", "window_average_8"]},
    }
    fc = forecast_dataset(tiny_tables, tiny_window, app, run.scoring, 1)
    cap = capacity_forecast(tiny_tables, tiny_window, app, 1)
    mode = ModeParams.from_mode_config("STABILITY", run.mode_config, run.app["optimize"])
    inp = build_inputs(tiny_tables, fc, cap, mode, run.app["optimize"])
    assert inp.M == 3 and inp.regions == ["R_N", "R_S"] and inp.accounts == ["ACC_DIST", "ACC_REST"]
    assert (inp.cap_in > 0).all() and inp.d2c_eligible.all()
    res = solve_allocation(inp, mode)
    assert res.optimal
    alloc = res.allocation()
    assert alloc.height == 3 * (2 + 2)
    assert (alloc["allocated_kg"] <= alloc["demand_kg"] + 1e-6).all()
    assert "capacity_m0" in res.duals

"""T5.5: soft constraints are reported; infeasibility is diagnosed, with fallback."""

from __future__ import annotations

import numpy as np
import pytest

from dce.optimize.coman import CoManInputs
from dce.optimize.diagnostics import diagnose
from dce.optimize.inputs import ModeParams
from dce.optimize.lp import PlanResult
from dce.optimize.plan import solve_plan
from dce.tests.test_optimize_coman import STAB, partner
from dce.tests.test_optimize_lp import W0, inputs
from dce.tests.test_optimize_spend import curve, spend_inputs


def test_clean_plan_has_no_diagnostics() -> None:
    res = solve_plan(inputs([5000] * 3, [[800] * 3], [[800] * 3]), STAB)
    assert res.optimal and res.extras["slacks"].is_empty() and diagnose(res) == []


def test_floor_violation_reported() -> None:
    res = solve_plan(inputs([100], [[50]], [[200]]), STAB)
    d = diagnose(res)
    assert [x["kind"] for x in d] == ["b2b_floor"]
    assert d[0]["amount"] == pytest.approx(96.0) and "A0 month 0" in d[0]["message"]


def test_budget_cut_below_held_plan_is_soft_and_reported() -> None:
    inp = inputs([1e6] * 3, [[1000] * 3, [1000] * 3], [])
    sin = spend_inputs([curve("R0", 0.002, low=True), curve("R1", 0.002, low=True)],
                       [[40000] * 3] * 2, [1.0, 1.0])  # fmt: skip
    sin.budget = sin.budget * 0.7  # what-if: budget cut 30% below the held plans
    res = solve_plan(inp, ModeParams("X", exploration_share=0.0), sin)
    assert res.optimal
    kinds = {x["kind"] for x in diagnose(res)}
    assert kinds & {"budget", "spend_hold"}
    total_violation = res.extras["slacks"]["amount"].sum()
    assert total_violation == pytest.approx(0.3 * 80000 * 3, rel=1e-6)


def test_infeasible_forced_coman_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the current construction forcing is always feasible (it only binds where the
    partner is available and waste absorbs excess), so the fallback is defensive: simulate an
    infeasible first solve and check the retry drops the forcing and says so."""
    from dce.optimize import plan as plan_mod

    real = plan_mod._solve_once
    calls: list[dict[str, int]] = []

    def fake(inp, mode, spend, coman, tl, gap):
        calls.append(dict(coman.forced_from))
        out = real(inp, mode, spend, coman, tl, gap)
        if coman.forced_from:
            out.status = "Infeasible"
        return out

    monkeypatch.setattr(plan_mod, "_solve_once", fake)
    inp = inputs([1000] * 3, [[800] * 3], [[800] * 3])
    res = plan_mod.solve_plan(inp, STAB, coman=CoManInputs([partner()], W0, forced_from={"CM": 0}))
    assert calls == [{"CM": 0}, {}]
    assert res.optimal and any(d["kind"] == "fallback" for d in diagnose(res))


def test_non_optimal_status_is_diagnosed() -> None:
    fake = PlanResult(
        "Infeasible", None, inputs([1], [[1]], []), STAB, *(np.zeros((1, 1)),) * 5,
        np.zeros(1), np.zeros(1), {},
    )  # fmt: skip
    d = diagnose(fake)
    assert d[0]["severity"] == "error" and "Infeasible" in d[0]["message"]

"""T5.4: co-man MILP extension (lead time, min/max, min-active linking)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from dce.capacity.coman import CoManPartner
from dce.optimize.coman import CoManInputs
from dce.optimize.inputs import ModeParams
from dce.optimize.plan import solve_plan
from dce.tests.test_optimize_lp import W0, inputs


def partner(cid: str = "CM", avail: date = date(2020, 1, 6), lead: int = 0, mn: float = 50.0,
            mx: float = 300.0, cost: float = 700.0, min_weeks: int = 4, rel: float = 1.0) -> CoManPartner:  # fmt: skip
    return CoManPartner(
        cid, "cc", avail, lead, mn, mx, cost, min_weeks, np.array([rel]), (9.0, 1.0)
    )


STAB = ModeParams("STABILITY", w_pen=3.0, w_gw=0.6, b2b_service_floor=0.98)


def test_activates_when_short_and_profitable() -> None:
    inp = inputs([1000] * 3, [[800] * 3], [[800] * 3])  # 1600 demand vs 1000 capacity
    res = solve_plan(inp, STAB, coman=CoManInputs([partner()], W0))
    cm = res.extras["coman"]
    assert cm["active"].all()
    # 4 weeks × max 300 = 1200 kg/month available; 600 kg shortfall is covered
    assert cm["requested_kg"].to_numpy() == pytest.approx([600.0] * 3)
    assert (res.u.sum() + res.s.sum()) == pytest.approx(0.0, abs=1e-6)


def test_not_activated_when_unprofitable_or_not_needed() -> None:
    short = inputs([1000] * 3, [[800] * 3], [[800] * 3])
    pricey = solve_plan(short, STAB, coman=CoManInputs([partner(cost=5000.0)], W0))
    assert not pricey.extras["coman"]["active"].any()
    ample = inputs([5000] * 3, [[800] * 3], [[800] * 3])
    idle = solve_plan(ample, STAB, coman=CoManInputs([partner()], W0))
    assert not idle.extras["coman"]["active"].any()


def test_lead_time_blocks_early_months() -> None:
    inp = inputs([1000] * 3, [[800] * 3], [[800] * 3])
    res = solve_plan(inp, STAB, coman=CoManInputs([partner(lead=6)], W0))
    cm = res.extras["coman"].sort("month")
    assert cm["available_weeks"].to_list() == [0, 2, 4]
    assert not cm["active"][0] and cm["requested_kg"][0] == 0
    assert cm["requested_kg"][1] <= 2 * 300 + 1e-6


def test_availability_date_respected() -> None:
    inp = inputs([1000] * 3, [[800] * 3], [[800] * 3])
    late = partner(avail=W0 + timedelta(weeks=20))
    res = solve_plan(inp, STAB, coman=CoManInputs([late], W0))
    assert not res.extras["coman"]["active"].any()


def test_min_commit_and_min_active_linking() -> None:
    # shortage only in month 0; min_active 8 weeks ⇒ stay active in month 1 at ≥ min commit
    # (cheap partner so the forced, wasted month-1 volume doesn't make activation unprofitable)
    inp = inputs([1000, 5000, 5000], [[800] * 3], [[800] * 3])
    p = partner(mn=100.0, min_weeks=8, cost=300.0)
    res = solve_plan(inp, STAB, coman=CoManInputs([p], W0))
    cm = res.extras["coman"].sort("month")
    assert cm["active"].to_list()[:2] == [True, True]
    assert cm["requested_kg"][1] == pytest.approx(4 * 100.0)  # at minimum commitment
    assert not cm["active"][2]


def test_reliability_haircut_in_plan_supply() -> None:
    inp = inputs([1000] * 3, [[800] * 3], [[800] * 3])
    res = solve_plan(inp, STAB, coman=CoManInputs([partner(rel=0.8)], W0))
    cm = res.extras["coman"]
    assert cm["expected_delivered_kg"].to_numpy() == pytest.approx(
        cm["requested_kg"].to_numpy() * 0.8
    )
    assert cm["requested_kg"][0] == pytest.approx(600 / 0.8)


def test_already_active_skips_lead_time_and_forced_activation() -> None:
    inp = inputs([1000] * 3, [[800] * 3], [[800] * 3])
    p = partner(lead=8)
    res = solve_plan(inp, STAB, coman=CoManInputs([p], W0, already_active={"CM"}))
    assert res.extras["coman"]["available_weeks"].to_list() == [4, 4, 4]
    ample = inputs([5000] * 3, [[800] * 3], [[800] * 3])
    forced = solve_plan(ample, STAB, coman=CoManInputs([partner()], W0, forced_from={"CM": 1}))
    assert forced.extras["coman"].sort("month")["active"].to_list() == [False, True, True]


def test_min_run_can_make_activation_unprofitable() -> None:
    """Same shortage, pricier partner: the forced second month tips the decision to 'no'."""
    inp = inputs([1000, 5000, 5000], [[800] * 3], [[800] * 3])
    p = partner(mn=100.0, min_weeks=8, cost=700.0)
    res = solve_plan(inp, STAB, coman=CoManInputs([p], W0))
    assert not res.extras["coman"]["active"].any()
    one_month = solve_plan(
        inp, STAB, coman=CoManInputs([partner(mn=100.0, min_weeks=4, cost=700.0)], W0)
    )
    assert one_month.extras["coman"].sort("month")["active"].to_list() == [True, False, False]

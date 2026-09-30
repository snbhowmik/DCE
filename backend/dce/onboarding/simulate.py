"""B2B onboarding simulator (ARCH §5.10, PRD FR-21, US4, T7.1).

A candidate account is added to the plan as a contracted B2B line (its committed volume is its
demand on every simulated future), then the whole horizon is re-solved and stress-tested for each
start month × ramp profile and compared with the plan without it. Recommendation classes:
`accept_now` · `accept_from` (later start) · `phase` (ramped) · `decline`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from dce.optimize.inputs import PlanInputs

CANDIDATE_ID = "CANDIDATE"
RAMPS: dict[str, tuple[float, ...]] = {
    "full": (1.0,),
    "50_100": (0.5, 1.0),
    "33_66_100": (1 / 3, 2 / 3, 1.0),
}


class Candidate(BaseModel):
    volume_kg_per_month: float = Field(gt=0, le=1e6)
    price_inr_per_kg: float = Field(gt=0, le=1e5)
    penalty_inr_per_kg: float = Field(default=0.0, ge=0, le=1e5)
    region_id: str
    start_month: int = Field(default=0, ge=0, le=11)  # 0 = first plan month
    ramp: str = Field(default="full", pattern="^(full|50_100|33_66_100)$")
    reach: float = Field(default=0.5, ge=0, le=1)  # relative reach (outlets / visibility)


def schedule(c: Candidate, n_months: int) -> np.ndarray:
    """Committed kg per plan month: zero before start, then the ramp profile."""
    r = RAMPS[c.ramp]
    return np.array(
        [0.0 if m < c.start_month else c.volume_kg_per_month * r[min(m - c.start_month, len(r) - 1)]
         for m in range(n_months)]
    )  # fmt: skip


def add_candidate(inp: PlanInputs, c: Candidate) -> PlanInputs:
    """PlanInputs with the candidate appended as the last B2B account."""
    sch = schedule(c, inp.M)
    return replace(
        inp,
        accounts=[*inp.accounts, CANDIDATE_ID],
        account_region=[*inp.account_region, c.region_id],
        commit=np.vstack([inp.commit, sch]),
        p_b2b=np.append(inp.p_b2b, c.price_inr_per_kg),
        penalty=np.append(inp.penalty, c.penalty_inr_per_kg),
        reach=np.append(inp.reach, c.reach),
        b2b_eligible=np.vstack([inp.b2b_eligible, sch > 0]),
    )


def candidate_b2b_paths(c: Candidate, inp: PlanInputs, n_paths: int) -> np.ndarray:
    """[1, P, M] stress-test demand for the candidate (contracted volume on every path)."""
    return np.broadcast_to(schedule(c, inp.M), (1, n_paths, inp.M)).copy()


def candidate_weekly(c: Candidate, inp: PlanInputs, horizon: list[Any]) -> np.ndarray:
    """[H] weekly kg for the risk detector (monthly volume spread over the month's weeks)."""
    pos = {w: i for i, w in enumerate(horizon)}
    out = np.zeros(len(horizon))
    for m, kg in zip(inp.months, schedule(c, inp.M), strict=True):
        for w in m.weeks:
            out[pos[w]] = kg / len(m.weeks)
    return out


def _inr(x: float) -> str:
    a = abs(x)
    v = f"₹{a / 1e7:.2f} Cr" if a >= 1e7 else f"₹{a / 1e5:.1f} L" if a >= 1e5 else f"₹{a:,.0f}"
    return ("-" if x < 0 else "") + v


def recommend(base: dict[str, float], options: list[dict[str, Any]], mode: str,
              concentration_cap: float) -> dict[str, Any]:  # fmt: skip
    """Pick the best acceptable option and classify it; reasons are plain language."""
    tol_fill = 0.005 if mode == "STABILITY" else 0.02  # existing-B2B fill loss we accept
    tol_short = 0.05 if mode == "STABILITY" else 0.15  # extra P(any B2B short) we accept
    ok = []
    for o in options:
        why = []
        if o["delta"]["contribution_inr"] <= 0:
            why.append("does not add contribution")
        if base["b2b_fill_existing"] - o["b2b_fill_existing"] > tol_fill:
            why.append("cuts fill for existing B2B accounts")
        if o["p_any_b2b_shortfall"] - base["p_any_b2b_shortfall"] > tol_short:
            why.append("raises shortfall risk beyond this strategy's tolerance")
        if o["capacity_share"] > concentration_cap + 1e-9:
            why.append(f"needs {o['capacity_share']:.0%} of capacity (cap {concentration_cap:.0%})")
        o["acceptable"] = not why
        o["issues"] = why
        if not why:
            ok.append(o)
    if not ok:
        best = max(options, key=lambda o: o["delta"]["contribution_inr"])
        msg = "no start month or ramp is acceptable: " + "; ".join(best["issues"])
        return {"class": "decline", "option": best, "reasons": [msg]}
    best = max(ok, key=lambda o: o["delta"]["contribution_inr"])
    first = min(o["start_month"] for o in options)
    cls = (
        "phase" if best["ramp"] != "full"
        else "accept_now" if best["start_month"] == first
        else "accept_from"
    )  # fmt: skip
    reasons = [
        f"adds expected contribution of {_inr(best['delta']['contribution_inr'])}",
        f"existing B2B fill stays at {best['b2b_fill_existing']:.1%}",
        f"candidate is served {best['candidate_fill']:.1%} of its volume",
    ]
    if cls != "accept_now":
        now = [o for o in options if o["start_month"] == first and o["ramp"] == "full"]
        if now and not now[0]["acceptable"]:
            reasons.append("starting at full volume now: " + "; ".join(now[0]["issues"]))
    return {"class": cls, "option": best, "reasons": reasons}

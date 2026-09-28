"""HiGHS via PuLP, with dual extraction (ARCH §5.7).

PuLP 2.9's HiGHS interface does not populate `constraint.pi`; rows are added in
`lp.constraints` order, so duals are read from the live `highspy.Highs` model in that order.
For a maximization PuLP negates the objective, hence shadow price = −row_dual.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pulp


@dataclass
class SolveResult:
    status: str
    objective: float | None
    is_mip: bool
    duals: dict[str, float] = field(default_factory=dict)


def solve(prob: pulp.LpProblem, *, time_limit: float = 10.0, mip_gap: float = 0.005) -> SolveResult:
    solver = pulp.HiGHS(msg=False, threads=1, timeLimit=time_limit, gapRel=mip_gap)
    code = prob.solve(solver)
    status = pulp.LpStatus[code]
    is_mip = any(v.cat == pulp.LpInteger for v in prob.variables())
    duals: dict[str, float] = {}
    if status == "Optimal" and not is_mip:
        sol = prob.solverModel.getSolution()
        sign = -1.0 if prob.sense == pulp.LpMaximize else 1.0
        duals = {name: sign * d for name, d in zip(prob.constraints, sol.row_dual, strict=True)}
    obj = pulp.value(prob.objective) if status == "Optimal" else None
    return SolveResult(status=status, objective=obj, is_mip=is_mip, duals=duals)

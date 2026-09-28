"""Core allocation LP: ARCH §5.7 constraints 1–5, 9, 10 (T5.1). Spend and co-man extend it.

max  Σ w_rev·(p_d2c·x + p_a·y) − w_pen·π·s − w_gw·g·u − h·waste + w_reach·p̄·reach·y
     − floor_penalty·f
s.t. (1) Σx + Σy + inv[m] + waste[m] = Cap[m] + inv[m−1]
     (2) x[r,m] + u[r,m] = D[r,m]
     (3) y[a,m] + s[a,m] = Commit[a,m]
     (4) y[a,m] + f[a,m] ≥ φ·Commit[a,m]           (soft; f reported)
     (5) y[a,m] ≤ s_max·Cap[m]
     (9) inv[m] ≤ carry_limit[m]
     (10) x, y fixed to 0 where the product matrix makes them ineligible
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
import pulp

from dce.optimize.inputs import ModeParams, PlanInputs
from dce.optimize.solver import SolveResult, solve


@dataclass
class LpModel:
    prob: pulp.LpProblem
    x: dict[tuple[int, int], pulp.LpVariable]
    u: dict[tuple[int, int], pulp.LpVariable]
    y: dict[tuple[int, int], pulp.LpVariable]
    s: dict[tuple[int, int], pulp.LpVariable]
    f: dict[tuple[int, int], pulp.LpVariable]
    inv: dict[int, pulp.LpVariable]
    waste: dict[int, pulp.LpVariable]
    supply_extra: dict[int, Any] = field(default_factory=dict)  # co-man etc. add to supply
    d2c_extra: dict[tuple[int, int], Any] = field(default_factory=dict)  # spend lift adds demand
    objective_terms: list[Any] = field(default_factory=list)


def build_lp(inp: PlanInputs, mode: ModeParams) -> LpModel:
    R, A, M = len(inp.regions), len(inp.accounts), inp.M
    prob = pulp.LpProblem("allocation", pulp.LpMaximize)
    x = {(r, m): pulp.LpVariable(f"x_{r}_{m}", 0) for r in range(R) for m in range(M)}
    u = {(r, m): pulp.LpVariable(f"u_{r}_{m}", 0) for r in range(R) for m in range(M)}
    y = {(a, m): pulp.LpVariable(f"y_{a}_{m}", 0) for a in range(A) for m in range(M)}
    s = {(a, m): pulp.LpVariable(f"s_{a}_{m}", 0) for a in range(A) for m in range(M)}
    f = {(a, m): pulp.LpVariable(f"f_{a}_{m}", 0) for a in range(A) for m in range(M)}
    inv = {m: pulp.LpVariable(f"inv_{m}", 0) for m in range(M)}
    waste = {m: pulp.LpVariable(f"waste_{m}", 0) for m in range(M)}
    for (r, m), v in x.items():
        if not inp.d2c_eligible[r, m]:
            v.upBound = 0.0  # (10)
    for (a, m), v in y.items():
        if not inp.b2b_eligible[a, m]:
            v.upBound = 0.0  # (10)
    return LpModel(prob, x, u, y, s, f, inv, waste)


def finalize_lp(model: LpModel, inp: PlanInputs, mode: ModeParams) -> None:
    """Add constraints and objective (after extensions registered supply/demand terms)."""
    prob, x, u, y, s, f, inv, waste = (
        model.prob, model.x, model.u, model.y, model.s, model.f, model.inv, model.waste,
    )  # fmt: skip
    R, A, M = len(inp.regions), len(inp.accounts), inp.M
    p_bar = float(np.mean(np.concatenate([inp.p_d2c, inp.p_b2b]))) if R + A else 0.0

    obj = []
    for (r, m), v in x.items():
        obj.append(mode.w_rev * inp.p_d2c[r] * v - mode.w_gw * inp.goodwill[r] * u[r, m])
    for (a, m), v in y.items():
        obj.append(mode.w_rev * inp.p_b2b[a] * v - mode.w_pen * inp.penalty[a] * s[a, m])
        obj.append(mode.w_reach * p_bar * inp.reach[a] * v)
        obj.append(-inp.floor_penalty * f[a, m])
    for m in range(M):
        obj.append(-inp.waste_cost * waste[m])
    prob += pulp.lpSum(obj + model.objective_terms)

    for m in range(M):
        prev = inv[m - 1] if m > 0 else inp.initial_inventory
        supply = inp.cap_in[m] + prev + model.supply_extra.get(m, 0)
        used = pulp.lpSum(x[r, m] for r in range(R)) + pulp.lpSum(y[a, m] for a in range(A))
        prob += (used + inv[m] + waste[m] == supply), f"capacity_m{m}"  # (1)
        prob += (inv[m] <= inp.carry_limit[m]), f"carry_m{m}"  # (9)
        for r in range(R):
            demand = inp.d2c_demand[r, m] + model.d2c_extra.get((r, m), 0)
            prob += (x[r, m] + u[r, m] == demand), f"d2c_r{r}_m{m}"  # (2)
        for a in range(A):
            c = inp.commit[a, m]
            prob += (y[a, m] + s[a, m] == c), f"b2b_a{a}_m{m}"  # (3)
            if inp.b2b_eligible[a, m]:
                floor = mode.b2b_service_floor * c
                prob += (y[a, m] + f[a, m] >= floor), f"floor_a{a}_m{m}"  # (4)
            cap_total = inp.cap_in[m] + model.supply_extra.get(m, 0)
            prob += (y[a, m] <= inp.concentration_cap * cap_total), f"conc_a{a}_m{m}"  # (5)


@dataclass
class PlanResult:
    status: str
    objective: float | None
    inputs: PlanInputs
    mode: ModeParams
    x: np.ndarray  # [R, M]
    u: np.ndarray
    y: np.ndarray  # [A, M]
    s: np.ndarray
    floor_slack: np.ndarray  # [A, M]
    inv: np.ndarray  # [M]
    waste: np.ndarray  # [M]
    duals: dict[str, float]
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def optimal(self) -> bool:
        return self.status == "Optimal"

    def allocation(self) -> pl.DataFrame:
        inp = self.inputs
        rows: list[tuple[Any, ...]] = []
        for m in inp.months:
            for r, reg in enumerate(inp.regions):
                d = self.x[r, m.idx] + self.u[r, m.idx]
                rows.append(
                    ("D2C", reg, None, m.idx, m.start, float(d), float(self.x[r, m.idx]),
                     float(self.u[r, m.idx]), bool(inp.d2c_eligible[r, m.idx]), 0.0)
                )  # fmt: skip
            for a, acc in enumerate(inp.accounts):
                rows.append(
                    ("B2B", inp.account_region[a], acc, m.idx, m.start,
                     float(inp.commit[a, m.idx]), float(self.y[a, m.idx]),
                     float(self.s[a, m.idx]), bool(inp.b2b_eligible[a, m.idx]),
                     float(self.floor_slack[a, m.idx]))
                )  # fmt: skip
        df = pl.DataFrame(
            rows,
            schema=["channel", "region_id", "account_id", "month", "month_start", "demand_kg",
                    "allocated_kg", "unmet_kg", "eligible", "floor_slack_kg"],
            orient="row",
        )  # fmt: skip
        return df.with_columns(
            pl.when(pl.col("demand_kg") > 0)
            .then(pl.col("allocated_kg") / pl.col("demand_kg"))
            .otherwise(None)
            .alias("fill_rate")
        )

    def floor_violations(self) -> pl.DataFrame:
        a = self.allocation()
        return a.filter(pl.col("floor_slack_kg") > 1e-6)


def _vals(d: dict[Any, pulp.LpVariable], shape: tuple[int, ...]) -> np.ndarray:
    out = np.zeros(shape)
    for k, v in d.items():
        out[k] = v.value() or 0.0
    return out


def extract(model: LpModel, res: SolveResult, inp: PlanInputs, mode: ModeParams) -> PlanResult:
    R, A, M = len(inp.regions), len(inp.accounts), inp.M
    return PlanResult(
        status=res.status,
        objective=res.objective,
        inputs=inp,
        mode=mode,
        x=_vals(model.x, (R, M)),
        u=_vals(model.u, (R, M)),
        y=_vals(model.y, (A, M)),
        s=_vals(model.s, (A, M)),
        floor_slack=_vals(model.f, (A, M)),
        inv=np.array([model.inv[m].value() or 0.0 for m in range(M)]),
        waste=np.array([model.waste[m].value() or 0.0 for m in range(M)]),
        duals=res.duals,
    )


def solve_allocation(
    inp: PlanInputs, mode: ModeParams, *, time_limit: float = 10.0, mip_gap: float = 0.005
) -> PlanResult:
    model = build_lp(inp, mode)
    finalize_lp(model, inp, mode)
    res = solve(model.prob, time_limit=time_limit, mip_gap=mip_gap)
    return extract(model, res, inp, mode)

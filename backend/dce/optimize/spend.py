"""Marketing spend as a decision variable inside the allocation LP (ARCH §5.7, T5.3).

Per D2C region r and month m, spend is split over the concave response segments k:
    sp[r,k,m] ≤ width[r,k] · weeks(m)                     (segment widths are ₹/week)
    lift_m' = Σ_m L[m', m] · Σ_k slope[r,k] · sp[r,k,m]    (L: monthly lag matrix from θ)
D2C demand becomes  forecast − lift(plan) + lift(sp), so spend = plan reproduces the forecast.

(6) budget      Σ_{r,k} sp[r,k,m] ≤ B[m]
(7) evidence    regions with RES < τ_mode may exceed plan only out of the exploration pool
                Σ_{gated r} extra[r,m] ≤ ε·B[m],  extra[r,m] ≥ Σ_k sp[r,k,m] − plan[r,m]
Low-confidence response fits: spend fixed at plan (ARCH §5.6); their only extra is exploration.
Segments are filled in order via binaries (exact PWL), so spend makes the model a small MILP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
import pulp

from dce.optimize.inputs import ModeParams, Month, PlanInputs
from dce.optimize.lp import LpModel
from dce.response.pwl import ResponseCurve


def monthly_lag_matrix(theta: float, months: list[Month]) -> np.ndarray:
    """L[m', m]: share of month-m spend effect landing in month m' (geometric weekly kernel,
    spend spread evenly over the month's weeks). Effects beyond the horizon are lost."""
    weeks = [w for m in months for w in m.weeks]
    month_of = np.array([m.idx for m in months for _ in m.weeks])
    H = len(weeks)
    kernel = (1 - theta) * theta ** np.arange(H) if theta > 0 else np.eye(1, H).ravel()
    M = len(months)
    L = np.zeros((M, M))
    for m in months:
        src = [i for i in range(H) if month_of[i] == m.idx]
        for i in src:
            for lag in range(H - i):
                L[month_of[i + lag], m.idx] += kernel[lag] / len(src)
    return L


@dataclass
class SpendInputs:
    curves: dict[str, ResponseCurve]  # by region
    theta: dict[str, float]
    planned: np.ndarray  # [R, M] ₹ planned per region-month (PlanInputs.regions order)
    res: dict[str, float]  # RES score by region (D2C)
    ltv_cac: dict[str, float]  # value per ₹ of acquisition spend
    budget: np.ndarray  # [M] ₹
    soft_penalty: float = 100.0  # per ₹ of budget overrun / relaxed hold (≫ any lift value)

    @property
    def budget_total(self) -> float:
        return float(self.budget.sum())


@dataclass
class SpendVars:
    sp: dict[tuple[int, int, int], pulp.LpVariable] = field(default_factory=dict)
    extra: dict[tuple[int, int], pulp.LpVariable] = field(default_factory=dict)
    gated: list[int] = field(default_factory=list)
    fixed: list[int] = field(default_factory=list)
    L: dict[int, np.ndarray] = field(default_factory=dict)


def add_spend(model: LpModel, inp: PlanInputs, sin: SpendInputs, mode: ModeParams) -> SpendVars:
    """Register spend variables, lift terms, budget and evidence-gate constraints."""
    prob = model.prob
    R, M = len(inp.regions), inp.M
    wk = np.array([len(m.weeks) for m in inp.months], dtype=float)
    v = SpendVars()
    total_sp: dict[tuple[int, int], Any] = {}
    for r, reg in enumerate(inp.regions):
        curve = sin.curves.get(reg)
        if curve is None:
            continue  # no response model: spend is not a lever here
        L = monthly_lag_matrix(sin.theta.get(reg, 0.0), inp.months)
        v.L[r] = L
        lift_by_month: dict[int, Any] = {}
        for m in range(M):
            vars_k = []
            for k in range(len(curve.slopes)):
                var = pulp.LpVariable(f"sp_{r}_{k}_{m}", 0, float(curve.widths[k] * wk[m]))
                v.sp[r, k, m] = var
                vars_k.append((curve.slopes[k], var))
            # Ordered filling (exact PWL): segment k+1 only once segment k is full. Needed because
            # extra lift can have negative value (unserved demand), where the LP relaxation of a
            # concave PWL would book spend into flat segments first (D-041).
            for k in range(len(vars_k) - 1):
                z = pulp.LpVariable(f"spz_{r}_{k}_{m}", cat="Binary")
                w_k = float(curve.widths[k] * wk[m])
                w_k1 = float(curve.widths[k + 1] * wk[m])
                prob += (vars_k[k][1] >= w_k * z), f"spfill_r{r}_k{k}_m{m}"
                prob += (vars_k[k + 1][1] <= w_k1 * z), f"sporder_r{r}_k{k}_m{m}"
            total_sp[r, m] = pulp.lpSum(var for _, var in vars_k)
            lift_by_month[m] = pulp.lpSum(float(sl) * var for sl, var in vars_k)
        # Planned-spend lift (constant) under the same PWL + lag, to keep sp = plan ⇒ forecast.
        plan_lift = curve.evaluate(sin.planned[r] / wk) * wk  # [M] kg if planned sustained
        for m2 in range(M):
            decided = pulp.lpSum(float(L[m2, m]) * lift_by_month[m] for m in range(M))
            baseline = float(L[m2] @ plan_lift)
            model.d2c_extra[r, m2] = decided - baseline
        fixed = curve.low_confidence
        gated = sin.res.get(reg, -np.inf) < mode.res_gate
        for m in range(M):
            ex = pulp.LpVariable(f"spx_{r}_{m}", 0)
            v.extra[r, m] = ex
            prob += (ex >= total_sp[r, m] - float(sin.planned[r, m])), f"spend_extra_r{r}_m{m}"
            if fixed:
                # held at plan: no cut, expansion only via the exploration pool
                hold = min(float(sin.planned[r, m]), float(curve.cap * wk[m]))
                h = model.soft(
                    f"spend_hold_r{r}_m{m}",
                    sin.soft_penalty,
                    "spend_hold",
                    f"{reg} month {m}: low-confidence spend cut below plan",
                )
                prob += (total_sp[r, m] + h >= hold), f"spend_hold_r{r}_m{m}"
        if fixed:
            v.fixed.append(r)
        if gated or fixed:
            v.gated.append(r)
        # objective: spend cost, D2C_EXPANSION customer value (≈ LTV:CAC per ₹)
        lc = float(sin.ltv_cac.get(reg, 0.0) or 0.0)
        for m in range(M):
            model.objective_terms.append(-mode.w_spend * total_sp[r, m])
            if mode.w_cust > 0 and lc > 0:
                model.objective_terms.append(mode.w_cust * lc * v.extra[r, m])

    for m in range(M):
        if any((r, m) in total_sp for r in range(R)):
            over = model.soft(
                f"budget_m{m}", sin.soft_penalty, "budget", f"month {m}: spend above budget"
            )
            prob += (
                (
                    pulp.lpSum(total_sp[r, m] for r in range(R) if (r, m) in total_sp)
                    <= float(sin.budget[m]) + over
                ),
                f"budget_m{m}",
            )  # (6), soft
            # (7): gated / low-confidence regions share the exploration pool
            pool = pulp.lpSum(v.extra[r, m] for r in v.gated if (r, m) in v.extra)
            prob += (pool <= mode.exploration_share * float(sin.budget[m])), f"explore_m{m}"
    return v


def spend_table(v: SpendVars, inp: PlanInputs, sin: SpendInputs) -> pl.DataFrame:
    rows = []
    for r, reg in enumerate(inp.regions):
        for m in range(inp.M):
            keys = [k for k in v.sp if k[0] == r and k[2] == m]
            if not keys:
                continue
            total = sum(v.sp[k].value() or 0.0 for k in keys)
            rows.append(
                {
                    "region_id": reg,
                    "month": m,
                    "planned_inr": float(sin.planned[r, m]),
                    "recommended_inr": float(total),
                    "delta_inr": float(total - sin.planned[r, m]),
                    "low_confidence": r in v.fixed,
                    "gated": r in v.gated,
                    "res": sin.res.get(reg),
                    "spend_cap_inr": float(sin.curves[reg].cap * len(inp.months[m].weeks)),
                }
            )
    return pl.DataFrame(rows)

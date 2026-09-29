"""Plan explanations: shadow prices, binding constraints, per-line drivers (ARCH §5.7, T5.6).

Shadow prices come from the LP relaxation (ARCH §5.7): co-man binaries fixed at their optimum,
spend segment-order binaries relaxed. Every reason carries a stable `code` (NFR-7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import polars as pl
import pulp

from dce.optimize.lp import LpModel, PlanResult
from dce.optimize.solver import solve

TOL = 1e-6
# Demand equalities always carry a dual (the value of +1 kg of demand); they are definitions,
# not limits, so they stay in `duals` but are left out of the binding-constraint list.
DEFINITIONAL = {"d2c_demand", "b2b_demand"}


def relaxed_duals(model: LpModel, time_limit: float = 10.0) -> dict[str, float]:
    """LP relaxation → {constraint: shadow price}.

    Co-man activation binaries are fixed at their optimum (on/off decisions). Spend segment-order
    binaries are relaxed to [0, 1]: fixing them would also freeze which spend segments are open
    and zero out the budget's marginal value; relaxed, the spend block is the standard concave
    PWL LP whose duals are the marginal values whenever extra lift is worth something.
    """
    fixed: list[tuple[pulp.LpVariable, Any, Any, Any]] = []
    for v in model.prob.variables():
        if v.cat == pulp.LpInteger:
            fixed.append((v, v.cat, v.lowBound, v.upBound))
            if v.name.startswith("spz_"):
                v.cat, v.lowBound, v.upBound = pulp.LpContinuous, 0, 1
            else:
                val = round(v.value() or 0.0)
                v.cat, v.lowBound, v.upBound = pulp.LpContinuous, val, val
    try:
        res = solve(model.prob, time_limit=time_limit)
        return res.duals
    finally:
        for v, cat, lo, hi in fixed:  # restore the MILP
            v.cat, v.lowBound, v.upBound = cat, lo, hi


_KINDS = [
    (r"^capacity_m(\d+)$", "capacity", "value of +1 kg of supply in month {0} (₹/kg)"),
    (r"^carry_m(\d+)$", "carryover", "value of +1 kg of carryover room after month {0} (₹/kg)"),
    (r"^d2c_r(\d+)_m(\d+)$", "d2c_demand", "value of +1 kg D2C demand in {r} month {1} (₹/kg)"),
    (r"^b2b_a(\d+)_m(\d+)$", "b2b_demand", "value of +1 kg orders from {a} month {1} (₹/kg)"),
    (r"^floor_a(\d+)_m(\d+)$", "b2b_floor", "cost of {a}'s service floor, month {1} (₹/kg)"),
    (r"^conc_a(\d+)_m(\d+)$", "concentration", "value of +1 kg concentration room, {a} m{1}"),
    (r"^budget_m(\d+)$", "budget", "value of +₹1 marketing budget in month {0} (₹/₹)"),
    (r"^explore_m(\d+)$", "exploration", "value of +₹1 exploration pool in month {0} (₹/₹)"),
    (r"^spend_hold_r(\d+)_m(\d+)$", "spend_hold", "cost of holding {r}'s spend at plan, month {1}"),
    (r"^coman_max_j(\d+)_m(\d+)$", "coman_max", "value of +1 kg co-man ceiling, partner {j} m{1}"),
]


def _describe(name: str, result: PlanResult) -> tuple[str, str] | None:
    inp = result.inputs
    for pattern, kind, template in _KINDS:
        m = re.match(pattern, name)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        ctx: dict[str, str] = {}
        if kind in ("d2c_demand", "spend_hold"):
            ctx["r"] = inp.regions[g[0]]
        if kind in ("b2b_demand", "b2b_floor", "concentration"):
            ctx["a"] = inp.accounts[g[0]]
        if kind == "coman_max":
            ctx["j"] = str(g[0])
        return kind, template.format(*g, **ctx)
    return None


def binding_constraints(
    model: LpModel, result: PlanResult, duals: dict[str, float]
) -> pl.DataFrame:
    rows = []
    for name, c in model.prob.constraints.items():
        d = duals.get(name, 0.0)
        if abs(d) < TOL:
            continue
        slack = abs(c.value() or 0.0) if c.sense != pulp.LpConstraintEQ else 0.0
        if slack > 1e-4:
            continue
        desc = _describe(name, result)
        if desc is None or desc[0] in DEFINITIONAL:
            continue
        rows.append({"constraint": name, "kind": desc[0], "shadow_price": d, "meaning": desc[1]})
    schema = {
        "constraint": pl.String,
        "kind": pl.String,
        "shadow_price": pl.Float64,
        "meaning": pl.String,
    }
    return pl.DataFrame(rows, schema=schema).sort("kind", "constraint")


def _r(code: str, detail: str, value: float | None = None) -> dict[str, Any]:
    return {"code": code, "detail": detail, "value": value}


def line_drivers(result: PlanResult, duals: dict[str, float]) -> pl.DataFrame:
    """Reasons for every allocation line and spend line (machine-readable codes)."""
    inp, mode = result.inputs, result.mode
    rows: list[dict[str, Any]] = []
    for m in range(inp.M):
        cap_price = duals.get(f"capacity_m{m}", 0.0)
        for r, reg in enumerate(inp.regions):
            dem = result.x[r, m] + result.u[r, m]
            value = mode.w_rev * inp.p_d2c[r] + mode.w_gw * inp.goodwill[r]
            reasons = []
            if not inp.d2c_eligible[r, m]:
                reasons.append(_r("ineligible", "product matrix / cold chain excludes this region"))
            elif dem <= TOL or result.u[r, m] <= TOL:
                reasons.append(_r("fully_served", "all forecast D2C demand allocated"))
            else:
                reasons.append(
                    _r(
                        "capacity_bound",
                        f"capacity worth ₹{cap_price:,.0f}/kg this month vs this line's "
                        f"₹{value:,.0f}/kg (price + goodwill); higher-value lines served first",
                        cap_price,
                    )
                )
            rows.append({"channel": "D2C", "line": reg, "month": m, "reasons": reasons})
        for a, acc in enumerate(inp.accounts):
            c = inp.commit[a, m]
            y = result.y[a, m]
            value = mode.w_rev * inp.p_b2b[a] + mode.w_pen * inp.penalty[a]
            reasons = []
            if not inp.b2b_eligible[a, m]:
                reasons.append(_r("ineligible", "product matrix excludes this account's region"))
            elif c <= TOL or y >= c - TOL:
                reasons.append(_r("fully_served", "forecast orders fully allocated"))
            else:
                if result.floor_slack[a, m] > TOL:
                    reasons.append(
                        _r(
                            "floor_violated",
                            f"service floor {mode.b2b_service_floor:.0%} not met by "
                            f"{result.floor_slack[a, m]:,.0f} kg",
                            result.floor_slack[a, m],
                        )
                    )
                if abs(duals.get(f"conc_a{a}_m{m}", 0.0)) > TOL:
                    reasons.append(
                        _r("concentration_cap", f"capped at {inp.concentration_cap:.0%} of supply")
                    )
                if abs(y - mode.b2b_service_floor * c) < 1e-3:
                    reasons.append(
                        _r(
                            "at_service_floor",
                            f"served at the {mode.b2b_service_floor:.0%} floor; "
                            "remaining "
                            f"capacity is worth more elsewhere (₹{cap_price:,.0f}/kg vs "
                            f"₹{value:,.0f}/kg here)",
                            cap_price,
                        )
                    )
                if not reasons:
                    reasons.append(
                        _r(
                            "capacity_bound",
                            f"capacity worth ₹{cap_price:,.0f}/kg vs ₹{value:,.0f}/kg",
                            cap_price,
                        )
                    )
            rows.append({"channel": "B2B", "line": acc, "month": m, "reasons": reasons})
    spend = result.extras.get("spend")
    if spend is not None:
        for s in spend.iter_rows(named=True):
            m = s["month"]
            reasons = []
            if s["low_confidence"]:
                msg = "response model low-confidence: spend held at plan"
                reasons.append(_r("held_at_plan", msg))
            elif s["gated"]:
                reasons.append(
                    _r(
                        "evidence_gate",
                        f"RES {s['res']} below the mode's gate: "
                        "expansion limited to the exploration pool",
                    )
                )
            b = duals.get(f"budget_m{m}", 0.0)
            if abs(b) > TOL:
                reasons.append(_r("budget_binding", f"an extra ₹1 of budget is worth ₹{b:.2f}", b))
            if s["recommended_inr"] >= s["spend_cap_inr"] - 1:
                reasons.append(_r("extrapolation_cap", "at max observed weekly spend × factor"))
            if not reasons:
                delta = s["delta_inr"]
                direction = "raised" if delta > 1 else "cut" if delta < -1 else "kept"
                reasons.append(
                    _r(
                        "marginal_value",
                        f"spend {direction}: marginal lift value vs cost and capacity",
                    )
                )
            line = s["region_id"]
            rows.append({"channel": "SPEND", "line": line, "month": m, "reasons": reasons})
    return pl.DataFrame(rows)


@dataclass
class Explanation:
    duals: dict[str, float]
    binding: pl.DataFrame
    drivers: pl.DataFrame


def explain(result: PlanResult) -> Explanation:
    model: LpModel | None = result.extras.get("model")
    if model is None or not result.optimal:
        return Explanation({}, pl.DataFrame(), pl.DataFrame())
    duals = result.duals if result.duals else relaxed_duals(model)
    binding = binding_constraints(model, result, duals)
    return Explanation(duals, binding, line_drivers(result, duals))

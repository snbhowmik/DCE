"""Full plan solve: allocation LP + optional extensions (spend T5.3, co-man T5.4)."""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from dce.metrics.scores import EvidenceScores
from dce.optimize.coman import CoManInputs, add_coman, coman_table
from dce.optimize.inputs import ModeParams, PlanInputs
from dce.optimize.lp import PlanResult, build_lp, extract, finalize_lp
from dce.optimize.solver import solve
from dce.optimize.spend import SpendInputs, add_spend, spend_table
from dce.response.pwl import linearize
from dce.response.run import ResponseSet


def solve_plan(
    inp: PlanInputs,
    mode: ModeParams,
    spend: SpendInputs | None = None,
    coman: CoManInputs | None = None,
    *,
    time_limit: float = 10.0,
    mip_gap: float = 0.005,
) -> PlanResult:
    model = build_lp(inp, mode)
    sv = add_spend(model, inp, spend, mode) if spend is not None else None
    cv = add_coman(model, inp, coman, mode) if coman is not None else None
    finalize_lp(model, inp, mode)
    res = solve(model.prob, time_limit=time_limit, mip_gap=mip_gap)
    out = extract(model, res, inp, mode)
    if cv is not None and coman is not None and out.optimal:
        out.extras["coman"] = coman_table(cv, inp, coman)
    if sv is not None and spend is not None and out.optimal:
        out.extras["spend"] = spend_table(sv, inp, spend)
        out.extras["d2c_demand_effective"] = np.array(
            [
                [inp.d2c_demand[r, m] + _val(model.d2c_extra.get((r, m), 0)) for m in range(inp.M)]
                for r in range(len(inp.regions))
            ]
        )
    return out


def _val(expr: Any) -> float:
    try:
        return float(expr.value() or 0.0)
    except AttributeError:
        return float(expr)


def build_spend_inputs(
    tables: dict[str, pl.DataFrame],
    inp: PlanInputs,
    responses: ResponseSet,
    scores: EvidenceScores,
    app: dict[str, Any],
) -> SpendInputs:
    k = int(app.get("response", {}).get("pwl_segments", 6))
    curves = {r: linearize(f, k) for r, f in responses.fits.items() if r in inp.regions}
    theta = {r: f.theta for r, f in responses.fits.items()}
    week_month = {w: m.idx for m in inp.months for w in m.weeks}
    plan = (
        tables["marketing_plan"]
        .filter(pl.col("channel") == "D2C")
        .filter(pl.col("week_start").is_in(list(week_month)))
        .group_by("region_id", "week_start")
        .agg(pl.col("planned_spend_inr").sum())
    )
    planned = np.zeros((len(inp.regions), inp.M))
    for reg, wk, amt in plan.iter_rows():
        if reg in inp.regions:
            planned[inp.regions.index(reg), week_month[wk]] += float(amt)
    res_d2c = scores.res.filter(pl.col("channel") == "D2C")
    res = {r: float(v) for r, v in res_d2c.select("region_id", "res").iter_rows() if v is not None}
    ltv_cac = {}
    for r, ltv, cac in res_d2c.select("region_id", "ltv_window_inr", "cac_inr").iter_rows():
        if ltv is not None and cac:
            ltv_cac[r] = float(ltv) / float(cac)
    modeled = [i for i, r in enumerate(inp.regions) if r in curves]
    budget = planned[modeled].sum(axis=0) * float(app.get("optimize", {}).get("budget_factor", 1.0))
    return SpendInputs(
        curves=curves, theta=theta, planned=planned, res=res, ltv_cac=ltv_cac, budget=budget
    )


__all__ = ["build_spend_inputs", "solve_plan"]

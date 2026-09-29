"""Run payload: one JSON document per run (ARCH §5.11 `RunPayload`, D-051).

The payload is the contract between the pipeline, the API, the dashboard and the AI layer: every
number the UI shows or the narrative mentions comes from here, and the payload carries the run's
provenance (run_id, dataset hash, mode, seed) so each figure is traceable (NFR-3).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl

from dce.demand.reconstruct import weekly_demand
from dce.runner import PipelineOutputs

SCHEMA_VERSION = 1
HISTORY_WEEKS = 52


# ---------------------------------------------------------------- JSON helpers


def clean(v: Any) -> Any:
    """JSON-safe value: dates → ISO, numpy → python, NaN/inf → None, floats rounded."""
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [clean(x) for x in v]
    if isinstance(v, np.ndarray):
        return [clean(x) for x in v.tolist()]
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, float | np.floating):
        f = float(v)
        if not math.isfinite(f):
            return None
        return (round(f, 4) if abs(f) < 1000 else round(f, 1)) + 0.0  # no -0.0
    return v


def records(df: pl.DataFrame) -> list[dict[str, Any]]:
    return [clean(r) for r in df.to_dicts()]


def _q(a: np.ndarray, axis: int = 0) -> dict[str, list[float]]:
    q = np.quantile(a, (0.1, 0.5, 0.9), axis=axis)
    return {"q10": clean(q[0]), "q50": clean(q[1]), "q90": clean(q[2])}


# ---------------------------------------------------------------- sections


def _run_section(out: PipelineOutputs, meta: dict[str, Any]) -> dict[str, Any]:
    inp = out.inputs
    return {
        **meta,
        "mode": out.run.mode,
        "seed": out.run.seed,
        "history_window": [out.window[0], out.window[1]],
        "decision_week": out.forecast.horizon[0],
        "horizon": out.forecast.horizon,
        "months": [
            {"idx": m.idx, "start": m.start, "end": m.end, "n_weeks": len(m.weeks)}
            for m in inp.months
        ],
        "product_line": inp.meta.get("product_line"),
        "skus": inp.meta.get("skus", []),
        "regions": inp.regions,
        "accounts": inp.accounts,
        "plan_status": out.plan.status,
        "forecast_hash": out.forecast.artifact_hash(),
        "mode_config": out.run.mode_config,
    }


def _demand_history(out: PipelineOutputs, tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    inp = out.inputs
    hist = weekly_demand(tables["orders"], out.window[1])
    skus = inp.meta.get("skus") or hist["sku_id"].unique().to_list()
    hist = hist.filter(
        pl.col("sku_id").is_in(skus)
        & (
            ((pl.col("channel") == "D2C") & pl.col("region_id").is_in(inp.regions))
            | ((pl.col("channel") == "B2B") & pl.col("account_id").is_in(inp.accounts))
        )
        & (pl.col("week_start") > out.window[1] - timedelta(weeks=HISTORY_WEEKS))
    )
    return (
        hist.group_by("week_start", "channel")
        .agg(pl.col("demand_kg", "sales_kg").sum())
        .sort("week_start", "channel")
    )


def _output_history(out: PipelineOutputs, tables: dict[str, pl.DataFrame]) -> pl.DataFrame:
    b = tables["capacity_batches"]
    line = out.capacity.product_line
    if line and "product_line" in b.columns:
        b = b.filter(pl.col("product_line") == line)
    return (
        b.with_columns(pl.col("end_date").dt.truncate("1w").alias("week_start"))
        .filter(
            (pl.col("week_start") > out.window[1] - timedelta(weeks=HISTORY_WEEKS))
            & (pl.col("week_start") <= out.window[1])
        )
        .group_by("week_start")
        .agg(
            pl.col("actual_yield_kg").sum().alias("output_kg"),
            pl.col("planned_yield_kg").sum().alias("planned_kg"),
            (pl.col("outcome") != "success").sum().alias("failed_batches"),
            pl.len().alias("batches"),
        )
        .sort("week_start")
    )


def _demand_section(out: PipelineOutputs, tables: dict[str, pl.DataFrame]) -> dict[str, Any]:
    inp, f = out.inputs, out.forecast
    sids, paths = f.paths()
    idx = {s: i for i, s in enumerate(sids)}
    P, H = paths.shape[1], paths.shape[2]
    by_channel = {"D2C": np.zeros((P, H)), "B2B": np.zeros((P, H))}
    by_region: dict[str, np.ndarray] = {}
    by_account: dict[str, np.ndarray] = {}
    for sid, reg, acc, ch in f.series.select(
        "series_id", "region_id", "account_id", "channel"
    ).iter_rows():
        if sid not in idx:
            continue
        if ch == "D2C" and reg in inp.regions:
            by_channel["D2C"] += paths[idx[sid]]
            by_region[reg] = by_region.get(reg, np.zeros((P, H))) + paths[idx[sid]]
        elif ch == "B2B" and acc in inp.accounts:
            by_channel["B2B"] += paths[idx[sid]]
            by_account[acc] = paths[idx[sid]]
    total = by_channel["D2C"] + by_channel["B2B"]
    return {
        "unit": "kg/week",
        "forecast": {
            "total": _q(total),
            "D2C": _q(by_channel["D2C"]),
            "B2B": _q(by_channel["B2B"]),
        },
        "by_region": {r: _q(a) for r, a in sorted(by_region.items())},
        "by_account": {a: _q(v) for a, v in sorted(by_account.items())},
        "history": records(_demand_history(out, tables)),
        "out_of_scope": records(f.out_of_scope),
    }


def _capacity_section(out: PipelineOutputs, tables: dict[str, pl.DataFrame]) -> dict[str, Any]:
    c = out.capacity
    return {
        "unit": "kg/week",
        "inhouse": records(c.quantiles),
        "supply_with_plan": records(
            out.risk.weekly.select("week_start", "h", "supply_q10", "supply_q50", "supply_q90")
        ),
        "history": records(_output_history(out, tables)),
        "partners": [clean(p.summary()) for p in c.partners],
        "perishability": {
            "shelf_life_days": c.perishability.shelf_life_days,
            "carryover_weeks": c.perishability.carryover_weeks,
            "waste_cost_inr_per_kg": clean(c.perishability.waste_cost_inr_per_kg),
        },
        "planned_at_quantile": out.mode.q_capacity,
        "planned_monthly_kg": clean(out.inputs.cap_in),
    }


def _plan_section(out: PipelineOutputs) -> dict[str, Any]:
    plan = out.plan
    alloc = plan.allocation()
    drivers = out.explanation.drivers
    if drivers.height:
        alloc = alloc.with_columns(pl.coalesce("region_id", "account_id").alias("line")).join(
            drivers, on=["channel", "line", "month"], how="left"
        )
    ex = plan.extras
    binding = out.explanation.binding
    return {
        "status": plan.status,
        "objective": clean(plan.objective),
        "allocation": records(alloc),
        "spend": records(ex["spend"]) if isinstance(ex.get("spend"), pl.DataFrame) else [],
        "coman": records(ex["coman"]) if isinstance(ex.get("coman"), pl.DataFrame) else [],
        "slacks": records(ex["slacks"]) if isinstance(ex.get("slacks"), pl.DataFrame) else [],
        "binding": records(binding.filter(pl.col("shadow_price").abs() > 1e-6)),
        "waste_kg": clean(plan.waste),
        "carry_kg": clean(plan.inv),
    }


def _stress_section(out: PipelineOutputs) -> dict[str, Any]:
    return {
        "n_paths": out.scenarios.n_paths,
        "plan": out.stress.name,
        "summary": clean(out.stress.summary()),
        "comparison": records(out.comparison.table()),
    }


def _risk_section(out: PipelineOutputs) -> dict[str, Any]:
    r = out.risk
    return {
        "breach_threshold": r.breach_threshold,
        "surplus_share": r.surplus_share,
        "surplus_probability": r.surplus_probability,
        "weekly": records(r.weekly),
        "alerts": [clean(a.as_dict()) for a in r.alerts],
        "breach_week": clean(r.breach_week),
        "surplus_week": clean(r.surplus_week),
    }


FUNNEL_COLS = [
    "spend_inr",
    "unique_visitors",
    "leads",
    "conversions",
    "revenue_inr",
    "new_customers",
    "nps_responses",
    "promoters",
    "detractors",
    "bounces",
    "customers_lost",
    "customers_at_start",
]


def _markets_section(out: PipelineOutputs) -> list[dict[str, Any]]:
    s = out.scores
    res = s.res.filter(pl.col("channel") == "D2C")
    fm = s.funnel_monthly.filter(pl.col("channel") == "D2C")
    last = cast("date | None", fm["period_start"].max()) if fm.height else None
    recent = (
        fm.filter(pl.col("period_start") > last - timedelta(days=365)) if last is not None else fm
    )
    cols = [c for c in FUNNEL_COLS if c in recent.columns]
    agg = recent.group_by("region_id").agg(pl.col(cols).sum()) if recent.height else None
    agg_all = fm.group_by("region_id").agg(pl.col(cols).sum()) if fm.height else None
    first = fm["period_start"].min() if fm.height else None
    ltv = {r["region_id"]: r for r in s.region_ltv.to_dicts()}
    spend = out.plan.extras.get("spend")
    rows = []
    for reg in out.inputs.regions:
        r_res = res.filter(pl.col("region_id") == reg)
        f = agg.filter(pl.col("region_id") == reg).to_dicts() if agg is not None else []
        tot = f[0] if f else {}
        fa = agg_all.filter(pl.col("region_id") == reg).to_dicts() if agg_all is not None else []
        fit = out.responses.fits.get(reg)
        sp = spend.filter(pl.col("region_id") == reg) if isinstance(spend, pl.DataFrame) else None
        rows.append(
            clean(
                {
                    "region_id": reg,
                    "res": r_res.to_dicts()[0] if r_res.height else None,
                    "funnel_12m": _funnel_ratios(tot),
                    "funnel_all": _funnel_ratios(fa[0] if fa else {}),
                    "funnel_period": [first, last],
                    "ltv": ltv.get(reg),
                    "response": None
                    if fit is None
                    else {
                        "low_confidence": fit.low_confidence,
                        "reasons": fit.reasons,
                        "elasticity": fit.elasticity,
                        "lift_at_mean_kg": fit.lift_at_mean,
                        "lift_ci": list(fit.ci.get("lift_at_mean", (None, None))),
                        "r2": fit.r2,
                        "mean_spend_inr": fit.mean_spend,
                        "spend_cap_inr": fit.spend_cap,
                    },
                    "response_skipped": out.responses.skipped.get(reg),
                    "spend": records(sp) if sp is not None else [],
                }
            )
        )
    return rows


def _funnel_ratios(t: dict[str, Any]) -> dict[str, Any]:
    def ratio(a: str, b: str) -> float | None:
        num, den = t.get(a), t.get(b)
        return num / den if num and den else None  # 0 spend → undefined, not ₹0

    out = dict(t)
    out.pop("region_id", None)
    out |= {
        "bounce_rate": ratio("bounces", "unique_visitors"),
        "cpl_inr": ratio("spend_inr", "leads"),
        "conversion_rate": ratio("conversions", "leads"),
        "cac_inr": ratio("spend_inr", "new_customers"),
        "churn_rate": ratio("customers_lost", "customers_at_start"),
        "nps": (
            100 * (t["promoters"] - t["detractors"]) / t["nps_responses"]
            if t.get("nps_responses")
            else None
        ),
    }
    return out


def _accounts_section(out: PipelineOutputs) -> list[dict[str, Any]]:
    aqs = {r["account_id"]: r for r in out.scores.aqs.to_dicts()}
    alloc = out.plan.allocation().filter(pl.col("channel") == "B2B")
    rows = []
    for acc in out.forecast.b2b.accounts.to_dicts():
        a = alloc.filter(pl.col("account_id") == acc["account_id"])
        rows.append(
            clean(
                acc
                | {
                    "aqs": aqs.get(acc["account_id"]),
                    "in_plan": acc["account_id"] in out.inputs.accounts,
                    "allocation": records(
                        a.select("month", "demand_kg", "allocated_kg", "unmet_kg", "fill_rate")
                    ),
                }
            )
        )
    return rows


def _health_section(out: PipelineOutputs, validation: dict[str, Any] | None) -> dict[str, Any]:
    d2c = out.forecast.d2c
    sel = d2c.selection
    cov = d2c.coverage
    n = sel.height
    beats = sel.filter(pl.col("mase") < pl.col("baseline_mase")).height if n else 0
    return {
        "validation": None
        if validation is None
        else {
            k: validation.get(k)
            for k in ("ok", "n_errors", "n_warnings", "issues", "history", "contract_version")
        },
        "selection": records(sel),
        "model_counts": clean(
            dict(sel.group_by("model").len().sort("model").iter_rows()) if n else {}
        ),
        "share_beating_baseline": beats / n if n else None,
        "coverage": records(cov),
        "coverage_mean_raw": clean(cov["coverage_raw"].mean()) if cov.height else None,
        "coverage_mean_calibrated": clean(cov["coverage_calibrated"].mean())
        if cov.height
        else None,
        "scores": records(d2c.scores),
        "anomalies": records(d2c.anomalies),
        "b2b_ratio": records(out.forecast.b2b.ratio_distribution),
    }


def _kpis(out: PipelineOutputs) -> dict[str, Any]:
    s = out.stress.summary()
    alloc = out.plan.allocation()
    cm = out.plan.extras.get("coman")
    sp = out.plan.extras.get("spend")
    cmp = out.comparison.means()
    base = cmp.filter(pl.col("plan") != out.stress.name)
    best_base = base.sort("revenue_inr", descending=True).row(0, named=True) if base.height else {}
    return clean(
        {
            "demand_kg": alloc["demand_kg"].sum(),
            "allocated_kg": alloc["allocated_kg"].sum(),
            "unmet_kg": alloc["unmet_kg"].sum(),
            "d2c_allocated_kg": alloc.filter(pl.col("channel") == "D2C")["allocated_kg"].sum(),
            "b2b_allocated_kg": alloc.filter(pl.col("channel") == "B2B")["allocated_kg"].sum(),
            "capacity_planned_kg": float(out.inputs.cap_in.sum()),
            "revenue_inr": s["revenue_inr"],
            "contribution_inr": s["contribution_inr"],
            "d2c_fill_rate": s["d2c_fill_rate"],
            "b2b_fill_rate": s["b2b_fill_rate"],
            "p_any_b2b_shortfall": s["any_b2b_shortfall"]["mean"],
            "waste_kg": s["waste_kg"],
            "coman_requested_kg": float(cm["requested_kg"].sum())
            if isinstance(cm, pl.DataFrame)
            else 0.0,
            "coman_cost_inr": float(cm["cost_inr"].sum()) if isinstance(cm, pl.DataFrame) else 0.0,
            "spend_planned_inr": float(sp["planned_inr"].sum())
            if isinstance(sp, pl.DataFrame)
            else 0.0,
            "spend_recommended_inr": float(sp["recommended_inr"].sum())
            if isinstance(sp, pl.DataFrame)
            else 0.0,
            "n_breach_alerts": sum(a.kind == "breach" for a in out.risk.alerts),
            "n_surplus_alerts": sum(a.kind == "surplus" for a in out.risk.alerts),
            "best_baseline": best_base.get("plan"),
            "best_baseline_revenue_inr": best_base.get("revenue_inr"),
            "best_baseline_waste_kg": best_base.get("waste_kg"),
        }
    )


def build_payload(
    out: PipelineOutputs,
    tables: dict[str, pl.DataFrame],
    meta: dict[str, Any],
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`meta` carries provenance: run_id, world_id, dataset_hash, config_hash, git_sha, …"""
    return clean(
        {
            "schema_version": SCHEMA_VERSION,
            "run": _run_section(out, meta),
            "kpis": _kpis(out),
            "demand": _demand_section(out, tables),
            "capacity": _capacity_section(out, tables),
            "plan": _plan_section(out),
            "stress": _stress_section(out),
            "risk": _risk_section(out),
            "markets": _markets_section(out),
            "accounts": _accounts_section(out),
            "health": _health_section(out, validation),
        }
    )


def write_payload(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return path

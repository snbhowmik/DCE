"""Mitigation impact, ranking and act-by dates (ARCH §5.9, PRD FR-23–25, T6.2–T6.3).

For each alert of a run, every catalog mitigation is checked for feasibility (lead time ≤ weeks
until the alert), applied as what-if levers on the run's own forecast, re-solved and stress-tested,
and scored: risk removed (kg of expected shortfall, or kg of expected waste for surplus) per
(cash cost + harm_weight · harm). Mitigations the optimizer already applies are listed, not re-run.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import yaml

from dce import paths
from dce.scenario import Levers


def load_catalog() -> dict[str, Any]:
    return yaml.safe_load((paths.CONFIG_DIR / "mitigations.yaml").read_text())


def _levers(
    base: Levers, spec: dict[str, Any], alert: dict[str, Any], horizon: list[str]
) -> Levers:
    upd = {k: v for k, v in spec.items() if k != "from"}
    if spec.get("from") == "alert":
        upd["from_week"] = horizon.index(alert["start"]) + 1
    if "mode_overrides" in upd:
        upd["mode_overrides"] = (base.mode_overrides or {}) | upd["mode_overrides"]
    return base.model_copy(update=upd)


def evaluate(p: dict[str, Any], solve: Any) -> dict[str, Any]:
    """`p` is a run payload; `solve(levers) -> PipelineOutputs` re-solves on the run's upstream."""
    cat = load_catalog()
    run = p["run"]
    base = Levers(**(run.get("levers") or {"mode": run["mode"]}))
    horizon = run["horizon"]
    lead_partner = min((c["lead_time_weeks"] for c in p["capacity"]["partners"]), default=None)
    base_out = solve(base)

    def risk(out: Any) -> dict[str, float]:
        w = out.risk.weekly
        s = out.stress.summary()
        return {
            "shortfall_kg": float(w["expected_shortfall_kg"].sum()),
            "surplus_kg": float(w["expected_surplus_kg"].sum()),
            "waste_kg": s["waste_kg"]["mean"],
            "contribution_inr": s["contribution_inr"]["mean"],
            "b2b_fill_rate": s["b2b_fill_rate"]["mean"],
            "d2c_fill_rate": s["d2c_fill_rate"]["mean"],
        }

    b = risk(base_out)
    hw = float(cat.get("harm_weight_inr", 2e5))
    results = []
    for ai, alert in enumerate(p["risk"]["alerts"]):
        kind = "breach" if alert["kind"] == "breach" else "surplus"
        for m in cat[kind]:
            lead = m["lead_time_weeks"]
            lead_w = lead_partner if lead == "partner" else int(lead)
            row: dict[str, Any] = {
                "alert": ai,
                "alert_kind": alert["kind"],
                "alert_start": alert["start"],
                "id": m["id"],
                "name": m["name"],
                "effect": m["effect"],
                "reversible": m["reversible"],
                "harm": m["harm"],
                "lead_time_weeks": lead_w,
            }
            if m["levers"] is None:
                row["status"] = (
                    "in_plan" if m["id"] == "M3" and lead_w is not None else "not_applicable"
                )
                results.append(row)
                continue
            start = date.fromisoformat(alert["start"])
            row["act_by"] = (start - timedelta(weeks=lead_w or 0)).isoformat()
            if (lead_w or 0) > alert["weeks_until"]:
                row["status"] = "too_late"
                results.append(row)
                continue
            if m["id"] == "S2" and p["kpis"]["coman_requested_kg"] <= 0:
                row["status"] = "not_applicable"
                results.append(row)
                continue
            r = risk(solve(_levers(base, m["levers"], alert, horizon)))
            key = "shortfall_kg" if kind == "breach" else "waste_kg"
            benefit = b[key] - r[key]
            cost = b["contribution_inr"] - r["contribution_inr"]  # ₹ given up (negative = gain)
            row |= {
                "status": "ranked" if benefit > 1e-6 else "no_benefit",
                "risk_removed_kg": benefit,
                "cost_inr": cost,
                "delta_b2b_fill": r["b2b_fill_rate"] - b["b2b_fill_rate"],
                "delta_d2c_fill": r["d2c_fill_rate"] - b["d2c_fill_rate"],
                "score": benefit / ((max(cost, 0.0) + hw * m["harm"]) / 1e5),
                "levers": _levers(base, m["levers"], alert, horizon).model_dump(),
            }
            results.append(row)
    ranked = sorted(
        (r for r in results if r["status"] == "ranked"),
        key=lambda r: (r["alert"], -r["score"]),
    )
    for i, r in enumerate(ranked):
        r["rank"] = 1 + sum(1 for x in ranked[:i] if x["alert"] == r["alert"])
    return {"base": b, "mitigations": results}

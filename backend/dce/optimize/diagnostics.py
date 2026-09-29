"""Soft-constraint and infeasibility diagnostics (ARCH §5.7, PRD FR-14, T5.5)."""

from __future__ import annotations

from typing import Any

import polars as pl

from dce.optimize.lp import LpModel, PlanResult

_STATUS_HINTS = {
    "Infeasible": "hard constraints conflict (e.g. a forced co-man activation outside its "
    "availability window); soft constraints were already relaxed",
    "Unbounded": "objective unbounded: a variable lacks a bound (model bug)",
    "Not Solved": "solver stopped before optimality (time limit); the plan is not usable",
    "Undefined": "solver returned no solution",
}


def slack_report(model: LpModel, tol: float = 1e-6) -> pl.DataFrame:
    rows = [
        {
            "name": name,
            "kind": kind,
            "amount": float(var.value() or 0.0),
            "penalty_per_unit": pen,
            "description": desc,
        }
        for name, (var, pen, kind, desc) in model.slacks.items()
        if (var.value() or 0.0) > tol
    ]
    schema = {"name": pl.String, "kind": pl.String, "amount": pl.Float64,
              "penalty_per_unit": pl.Float64, "description": pl.String}  # fmt: skip
    return pl.DataFrame(rows, schema=schema)


def diagnose(result: PlanResult) -> list[dict[str, Any]]:
    """Human-readable issues with a solved (or failed) plan; empty when clean."""
    out: list[dict[str, Any]] = []
    if not result.optimal:
        out.append(
            {
                "severity": "error",
                "kind": "status",
                "message": f"solver status {result.status}: "
                + _STATUS_HINTS.get(result.status, "unknown"),
            }
        )
    for note in result.extras.get("fallbacks", []):
        out.append({"severity": "warning", "kind": "fallback", "message": note})
    slacks = result.extras.get("slacks")
    if slacks is not None:
        for r in slacks.iter_rows(named=True):
            unit = "kg" if r["kind"] == "b2b_floor" else "₹"
            out.append(
                {
                    "severity": "warning",
                    "kind": r["kind"],
                    "message": f"{r['description']} by {r['amount']:,.0f} {unit}",
                    "amount": r["amount"],
                }
            )
    return out

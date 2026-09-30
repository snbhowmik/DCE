"""What-if levers applied to a cached upstream stage, then re-solved (ARCH §5.11 executor, T9.3).

Levers transform *copies* of the forecast / capacity sample paths and the marketing plan, so the
optimizer, stress test, risk detector and payload all see one consistent scenario. The forecast
models are never refit and never see the strategy (P1); a scenario is a deterministic edit of
their outputs, re-solved in seconds.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import polars as pl
from pydantic import BaseModel, Field

from dce.capacity.model import capacity_quantiles
from dce.onboarding.simulate import Candidate
from dce.runner import UpstreamOutputs

PCT = Field(default=0.0, ge=-90.0, le=300.0)


class Levers(BaseModel):
    """Every field defaults to "no change". Percentages are relative (+20 = 20% more)."""

    mode: str = "STABILITY"
    mode_overrides: dict[str, Any] | None = None  # CUSTOM mode only (validated by dce.strategy)
    d2c_demand_pct: float = PCT
    b2b_demand_pct: float = PCT
    account_id: str | None = None  # one B2B account's orders change ...
    account_demand_pct: float = PCT  # ... by this much ...
    from_week: int = Field(
        default=1, ge=1, le=13
    )  # ... from this horizon week on (all demand levers)
    capacity_pct: float = Field(default=0.0, ge=-90.0, le=100.0)
    capacity_from_week: int = Field(default=1, ge=1, le=13)
    capacity_to_week: int = Field(default=13, ge=1, le=13)
    coman_available: bool = True
    budget_pct: float = Field(default=0.0, ge=-100.0, le=200.0)
    candidate: Candidate | None = None  # prospective B2B account (T7.1)

    def changes(self) -> list[str]:
        """Plain-language list of what this scenario changes (for logs and the UI)."""
        out = []
        if self.d2c_demand_pct:
            out.append(f"D2C demand {self.d2c_demand_pct:+g}% from week {self.from_week}")
        if self.b2b_demand_pct:
            out.append(f"B2B demand {self.b2b_demand_pct:+g}% from week {self.from_week}")
        if self.account_id and self.account_demand_pct:
            out.append(
                f"{self.account_id} orders {self.account_demand_pct:+g}% from week {self.from_week}"
            )
        if self.capacity_pct:
            out.append(
                f"in-house capacity {self.capacity_pct:+g}% in weeks "
                f"{self.capacity_from_week}–{self.capacity_to_week}"
            )
        if not self.coman_available:
            out.append("co-manufacturing unavailable")
        if self.budget_pct:
            out.append(f"marketing budget {self.budget_pct:+g}%")
        if self.mode_overrides:
            out.append("custom strategy weights")
        if self.candidate:
            c = self.candidate
            ramp = {"full": "full volume", "50_100": "ramp 50→100%", "33_66_100": "ramp 33→66→100%"}
            out.append(
                f"new B2B account {c.volume_kg_per_month:,.0f} kg/mo "
                f"at ₹{c.price_inr_per_kg:,.0f}/kg from month {c.start_month + 1}, {ramp[c.ramp]}"
            )
        return out


def _scale(paths: np.ndarray, factor: float, start: int, stop: int | None = None) -> np.ndarray:
    """Copy of [..., P, H] paths with weeks [start, stop) multiplied by factor."""
    out = paths.astype(float, copy=True)
    out[..., start:stop] *= factor
    return out


def apply_levers(
    lv: Levers, up: UpstreamOutputs, tables: dict[str, pl.DataFrame]
) -> tuple[UpstreamOutputs, dict[str, pl.DataFrame]]:
    """New upstream + tables with the levers applied; inputs are never mutated."""
    f, cap = up.forecast, up.capacity
    w0 = lv.from_week - 1
    d2c, b2b = f.d2c, f.b2b
    if lv.d2c_demand_pct and d2c.paths.size:
        d2c = replace(d2c, paths=_scale(d2c.paths, 1 + lv.d2c_demand_pct / 100, w0))
    if (lv.b2b_demand_pct or (lv.account_id and lv.account_demand_pct)) and b2b.paths_kg.size:
        paths = _scale(b2b.paths_kg, 1 + lv.b2b_demand_pct / 100, w0)
        if lv.account_id and lv.account_demand_pct:
            acc_of = dict(f.series.select("series_id", "account_id").iter_rows())
            idx = [i for i, s in enumerate(b2b.path_series) if acc_of.get(s) == lv.account_id]
            if not idx:
                raise ValueError(f"unknown or inactive B2B account {lv.account_id!r}")
            for i in idx:
                paths[i] = _scale(paths[i], 1 + lv.account_demand_pct / 100, w0)
        b2b = replace(b2b, paths_kg=paths)
    forecast = replace(f, d2c=d2c, b2b=b2b)

    if lv.capacity_pct or not lv.coman_available:
        paths = cap.paths
        if lv.capacity_pct:
            a, b = lv.capacity_from_week - 1, max(lv.capacity_to_week, lv.capacity_from_week)
            paths = _scale(paths, 1 + lv.capacity_pct / 100, a, b)
        cap = replace(
            cap,
            paths=paths,
            quantiles=capacity_quantiles(
                paths, cap.horizon, cap.quantiles["planned_kg"].to_numpy()
            ),
            partners=cap.partners if lv.coman_available else [],
        )

    if lv.budget_pct:
        mp = tables["marketing_plan"]
        horizon0 = f.horizon[0]
        tables = tables | {
            "marketing_plan": mp.with_columns(
                pl.when(pl.col("week_start") >= horizon0)
                .then(pl.col("planned_spend_inr") * (1 + lv.budget_pct / 100))
                .otherwise(pl.col("planned_spend_inr"))
                .alias("planned_spend_inr")
            )
        }
    return UpstreamOutputs(forecast=forecast, capacity=cap, responses=up.responses), tables

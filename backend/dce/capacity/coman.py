"""Co-manufacturing partners (ARCH §5.5, PRD FR-12, T3.2).

Deterministic once activated: output starts `lead_time_weeks` after activation, and activation
cannot precede `available_from`. Weekly volume is 0 or within [min_commit, max]. Delivered
volume = requested × reliability, where reliability is sampled per path × week from historical
delivered/requested ratios (capped at 1), or from a Beta prior when there is no history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from dce.seeds import rng as seeded_rng


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True)
class CoManPartner:
    coman_id: str
    product_line: str
    available_from: date
    lead_time_weeks: int
    min_kg_per_week: float
    max_kg_per_week: float
    unit_cost_inr_per_kg: float
    min_active_weeks: int
    reliability: np.ndarray  # empirical delivered/requested ratios (may be empty)
    prior: tuple[float, float]

    def earliest_activation(self, decision_week: date) -> date:
        return max(monday(decision_week), monday(self.available_from))

    def earliest_output(self, decision_week: date) -> date:
        return self.earliest_activation(decision_week) + timedelta(weeks=self.lead_time_weeks)

    def output_mask(self, horizon: list[date], decision_week: date) -> np.ndarray:
        """True for horizon weeks where output is possible if activated as early as allowed."""
        first = self.earliest_output(decision_week)
        return np.array([w >= first for w in horizon])

    def feasible_volume(self, kg: float) -> float:
        """Snap a requested weekly volume to 0 or [min, max]."""
        if kg <= 0:
            return 0.0
        return float(min(max(kg, self.min_kg_per_week), self.max_kg_per_week))

    @property
    def reliability_mean(self) -> float:
        if self.reliability.size:
            return float(self.reliability.mean())
        a, b = self.prior
        return a / (a + b)

    def sample_reliability(self, shape: tuple[int, ...], gen: np.random.Generator) -> np.ndarray:
        if self.reliability.size:
            return gen.choice(self.reliability, size=shape, replace=True)
        return gen.beta(*self.prior, size=shape)

    def delivered_paths(self, requested: np.ndarray, n_paths: int, seed: int) -> np.ndarray:
        """[n_paths, H] delivered kg for a requested weekly schedule [H]."""
        gen = seeded_rng(seed, "coman_reliability", self.coman_id)
        r = self.sample_reliability((n_paths, len(requested)), gen)
        return requested[None, :] * r

    def summary(self) -> dict[str, Any]:
        rel = self.reliability
        return {
            "coman_id": self.coman_id,
            "product_line": self.product_line,
            "available_from": self.available_from,
            "lead_time_weeks": self.lead_time_weeks,
            "min_kg_per_week": self.min_kg_per_week,
            "max_kg_per_week": self.max_kg_per_week,
            "unit_cost_inr_per_kg": self.unit_cost_inr_per_kg,
            "min_active_weeks": self.min_active_weeks,
            "reliability_n_weeks": int(rel.size),
            "reliability_mean": self.reliability_mean,
            "reliability_p10": float(np.quantile(rel, 0.1)) if rel.size else None,
            "reliability_from_prior": rel.size == 0,
        }


def load_partners(
    contracts: pl.DataFrame, activity: pl.DataFrame, prior: tuple[float, float] = (9.0, 1.0)
) -> list[CoManPartner]:
    ratios = (
        activity.filter(pl.col("requested_kg") > 0)
        .with_columns(
            (pl.col("delivered_kg") / pl.col("requested_kg")).clip(upper_bound=1.0).alias("r")
        )
        .group_by("coman_id")
        .agg(pl.col("r"))
    )
    by_id = {cid: np.array(r, dtype=float) for cid, r in ratios.iter_rows()}
    return [
        CoManPartner(
            coman_id=row["coman_id"],
            product_line=row["product_line"],
            available_from=row["available_from"],
            lead_time_weeks=int(row["lead_time_weeks"]),
            min_kg_per_week=float(row["min_commit_kg_per_week"]),
            max_kg_per_week=float(row["max_kg_per_week"]),
            unit_cost_inr_per_kg=float(row["unit_cost_inr_per_kg"]),
            min_active_weeks=int(row["min_active_weeks"]),
            reliability=by_id.get(row["coman_id"], np.array([])),
            prior=prior,
        )
        for row in contracts.sort("coman_id").iter_rows(named=True)
    ]

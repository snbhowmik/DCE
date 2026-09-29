"""Weekly breach and surplus detection from sample paths (ARCH §5.8, PRD FR-22/FR-25, T6.1).

    P_breach[w]  = mean over paths( demand[w] > supply[w] )
    P_surplus[w] = mean over paths( supply[w] − demand[w] > surplus_share · supply[w] )

Supply is in-house output plus the co-man the plan commits to (requested × sampled reliability);
demand is every in-scope D2C region and B2B account, including the plan's spend-driven D2C shift.
Carry-over is ignored, so the weekly test is conservative (perishable product, A-003).
A breach (surplus) week is a week whose probability reaches the mode's `breach_threshold`
(the configured `surplus_probability`); consecutive flagged weeks form one alert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from dce.capacity.coman import CoManPartner
from dce.capacity.model import CapacityForecast
from dce.forecast.run import DatasetForecast
from dce.optimize.inputs import PlanInputs
from dce.optimize.stress import PlanDecision
from dce.seeds import rng as seeded_rng


@dataclass(frozen=True)
class Alert:
    kind: str  # "breach" | "surplus"
    start: date
    end: date  # last flagged week (week start)
    weeks_until: int  # full weeks from the decision week to `start`
    peak_probability: float
    expected_kg: float  # expected shortfall (breach) or surplus over the episode

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "weeks_until": self.weeks_until,
            "peak_probability": self.peak_probability,
            "expected_kg": self.expected_kg,
        }


@dataclass
class RiskReport:
    weekly: pl.DataFrame
    breach_threshold: float
    surplus_share: float
    surplus_probability: float
    alerts: list[Alert]

    def first(self, kind: str) -> Alert | None:
        return next((a for a in self.alerts if a.kind == kind), None)

    @property
    def breach_week(self) -> date | None:
        a = self.first("breach")
        return a.start if a else None

    @property
    def surplus_week(self) -> date | None:
        a = self.first("surplus")
        return a.start if a else None


def detect(
    demand: np.ndarray,
    supply: np.ndarray,
    horizon: list[date],
    breach_threshold: float,
    surplus_share: float = 0.15,
    surplus_probability: float = 0.5,
) -> RiskReport:
    """demand, supply: [P, H] kg paths on the same horizon."""
    if demand.shape != supply.shape or demand.shape[1] != len(horizon):
        raise ValueError(f"shape mismatch: demand {demand.shape}, supply {supply.shape}")
    gap = demand - supply
    p_breach = (gap > 0).mean(axis=0)
    shortfall = np.maximum(gap, 0.0).mean(axis=0)
    p_surplus = (-gap > surplus_share * supply).mean(axis=0)
    surplus = np.maximum(-gap, 0.0).mean(axis=0)
    dq = np.quantile(demand, (0.1, 0.5, 0.9), axis=0)
    sq = np.quantile(supply, (0.1, 0.5, 0.9), axis=0)
    breach = p_breach >= breach_threshold
    surp = (p_surplus >= surplus_probability) & ~breach
    weekly = pl.DataFrame(
        {
            "week_start": horizon,
            "h": np.arange(1, len(horizon) + 1),
            "demand_q10": dq[0],
            "demand_q50": dq[1],
            "demand_q90": dq[2],
            "supply_q10": sq[0],
            "supply_q50": sq[1],
            "supply_q90": sq[2],
            "p_breach": p_breach,
            "expected_shortfall_kg": shortfall,
            "p_surplus": p_surplus,
            "expected_surplus_kg": surplus,
            "breach": breach,
            "surplus": surp,
        }
    )
    alerts = _episodes("breach", breach, p_breach, shortfall, horizon)
    alerts += _episodes("surplus", surp, p_surplus, surplus, horizon)
    alerts.sort(key=lambda a: (a.start, a.kind))
    return RiskReport(weekly, breach_threshold, surplus_share, surplus_probability, alerts)


def _episodes(
    kind: str, flag: np.ndarray, prob: np.ndarray, kg: np.ndarray, horizon: list[date]
) -> list[Alert]:
    out, w = [], 0
    while w < len(flag):
        if not flag[w]:
            w += 1
            continue
        e = w
        while e + 1 < len(flag) and flag[e + 1]:
            e += 1
        out.append(
            Alert(
                kind=kind,
                start=horizon[w],
                end=horizon[e],
                weeks_until=w,
                peak_probability=float(prob[w : e + 1].max()),
                expected_kg=float(kg[w : e + 1].sum()),
            )
        )
        w = e + 1
    return out


def weekly_demand_paths(
    inp: PlanInputs, forecast: DatasetForecast, d2c_shift: np.ndarray | None = None
) -> np.ndarray:
    """[P, H] total in-scope demand: D2C regions + B2B accounts in the plan, plus the plan's
    monthly D2C shift spread evenly over each month's weeks."""
    sids, paths = forecast.paths()
    idx = {s: i for i, s in enumerate(sids)}
    total = np.zeros(paths.shape[1:])
    for sid, reg, acc, ch in forecast.series.select(
        "series_id", "region_id", "account_id", "channel"
    ).iter_rows():
        keep = (ch == "D2C" and reg in inp.regions) or (ch == "B2B" and acc in inp.accounts)
        if keep and sid in idx:
            total += paths[idx[sid]]
    if d2c_shift is not None:
        total = total + _spread(d2c_shift.sum(axis=0), inp, forecast.horizon)[None, :]
    return np.maximum(total, 0.0)


def weekly_supply_paths(
    inp: PlanInputs,
    capacity: CapacityForecast,
    horizon: list[date],
    partners: list[CoManPartner],
    coman_q: np.ndarray,
    decision_week: date,
    n_paths: int,
    seed: int,
) -> np.ndarray:
    """[P, H] in-house paths (resampled to n_paths) + committed co-man delivered."""
    cap = capacity.paths
    if cap.shape[0] != n_paths:
        cap = cap[seeded_rng(seed, "risk_cap_align").integers(0, cap.shape[0], n_paths)]
    supply = cap.astype(float).copy()
    for j, p in enumerate(partners):
        if j >= len(coman_q) or not coman_q[j].any():
            continue
        mask = p.output_mask(horizon, decision_week)
        weekly = _spread(coman_q[j], inp, horizon, mask)
        supply += p.delivered_paths(weekly, n_paths, seed)
    return supply


def _spread(
    monthly: np.ndarray, inp: PlanInputs, horizon: list[date], mask: np.ndarray | None = None
) -> np.ndarray:
    """Spread [M] monthly kg evenly over each month's (allowed) weeks → [H]."""
    pos = {w: i for i, w in enumerate(horizon)}
    out = np.zeros(len(horizon))
    for m in inp.months:
        ix = [pos[w] for w in m.weeks if mask is None or mask[pos[w]]]
        if ix and monthly[m.idx]:
            out[ix] = monthly[m.idx] / len(ix)
    return out


def assess(
    inp: PlanInputs,
    forecast: DatasetForecast,
    capacity: CapacityForecast,
    decision: PlanDecision,
    partners: list[CoManPartner],
    breach_threshold: float,
    risk_cfg: dict[str, Any],
    seed: int,
) -> RiskReport:
    """Risk of the plan as committed (its co-man and spend decisions included)."""
    demand = weekly_demand_paths(inp, forecast, decision.d2c_shift)
    supply = weekly_supply_paths(
        inp,
        capacity,
        forecast.horizon,
        partners,
        decision.coman_q,
        forecast.horizon[0],
        demand.shape[0],
        seed,
    )
    return detect(
        demand,
        supply,
        forecast.horizon,
        breach_threshold,
        float(risk_cfg.get("surplus_share", 0.15)),
        float(risk_cfg.get("surplus_probability", 0.5)),
    )

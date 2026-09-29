"""Monte Carlo stress test of a fixed plan (ARCH §5.7 "Stress test", PRD FR-18, T5.7).

A plan is a set of monthly entitlements: planned kg per D2C region (x) and B2B account (y), plus
co-man volumes and spend decisions. Each sampled future (demand paths × capacity paths × co-man
reliability; independent in v1, A-009) is replayed month by month:

  supply  = in-house output + co-man delivered + carry-in
  1. B2B floors: φ × min(order, entitlement) per account (pro-rata if short)
  2. entitlements: min(demand, entitlement) per line, in priority order
  3. surplus: remaining demand of every line, in priority order
  4. leftover → carryover (≤ carry limit), rest → waste
Priority = the line's shadow price (value of +1 kg of its demand), falling back to its weighted
price + penalty/goodwill. The same replay scores the rule-based baselines (T5.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from dce.capacity.model import CapacityForecast
from dce.forecast.run import DatasetForecast
from dce.optimize.inputs import PlanInputs
from dce.optimize.lp import PlanResult
from dce.seeds import rng as seeded_rng

QS = (0.1, 0.5, 0.9)


def _monthly(paths: np.ndarray, inp: PlanInputs, horizon: list[date]) -> np.ndarray:
    """paths [..., P, H] → [..., P, M] monthly sums."""
    idx = {w: i for i, w in enumerate(horizon)}
    return np.stack([paths[..., [idx[w] for w in m.weeks]].sum(axis=-1) for m in inp.months], -1)


@dataclass
class Scenarios:
    """Sampled futures aligned to the plan's regions/accounts/months."""

    d2c: np.ndarray  # [R, P, M] kg
    b2b: np.ndarray  # [A, P, M] kg
    inhouse: np.ndarray  # [P, M] kg
    coman_reliability: np.ndarray  # [J, P, M] share of requested volume delivered

    @property
    def n_paths(self) -> int:
        return int(self.inhouse.shape[0])


def build_scenarios(
    inp: PlanInputs,
    forecast: DatasetForecast,
    capacity: CapacityForecast,
    partners: list[Any],
    seed: int,
) -> Scenarios:
    sids, paths = forecast.paths()
    horizon = forecast.horizon
    series = forecast.series
    P = paths.shape[1]
    d2c = np.zeros((len(inp.regions), P, inp.M))
    for sid, reg, ch in series.select("series_id", "region_id", "channel").iter_rows():
        if ch == "D2C" and reg in inp.regions:
            d2c[inp.regions.index(reg)] += _monthly(paths[sids.index(sid)], inp, horizon)
    b2b = np.zeros((len(inp.accounts), P, inp.M))
    b2b_series = series.filter(pl.col("channel") == "B2B").select("series_id", "account_id")
    for sid, acc in b2b_series.iter_rows():
        if acc in inp.accounts:
            b2b[inp.accounts.index(acc)] = _monthly(paths[sids.index(sid)], inp, horizon)
    cap = _monthly(capacity.paths, inp, horizon)
    if cap.shape[0] != P:  # align path counts (independent in v1: resample capacity paths)
        cap = cap[seeded_rng(seed, "stress_cap_align").integers(0, cap.shape[0], P)]
    rel = (
        np.stack([_monthly_reliability(p, inp, P, seed) for p in partners])
        if partners
        else np.zeros((0, P, inp.M))
    )
    return Scenarios(d2c=d2c, b2b=b2b, inhouse=cap, coman_reliability=rel)


def _monthly_reliability(partner: Any, inp: PlanInputs, n_paths: int, seed: int) -> np.ndarray:
    """[P, M] mean weekly delivered share per month for one co-man partner."""
    cols = []
    for m in inp.months:
        gen = seeded_rng(seed, "stress_rel", partner.coman_id, m.idx)
        cols.append(partner.sample_reliability((n_paths, len(m.weeks)), gen).mean(axis=1))
    return np.stack(cols, axis=-1)


@dataclass
class PlanDecision:
    """What is fixed when a plan is stress-tested."""

    name: str
    x: np.ndarray  # [R, M] D2C entitlements
    y: np.ndarray  # [A, M] B2B entitlements
    coman_q: np.ndarray  # [J, M] requested co-man kg
    coman_cost: np.ndarray  # [J] ₹/kg
    d2c_shift: np.ndarray  # [R, M] demand change from spend decisions (kg)
    spend: float  # total ₹ spend over the horizon
    # (channel, index, month) → value of +1 kg of that line's demand (solver shadow price)
    priority: dict[tuple[str, int, int], float] = field(default_factory=dict)

    @classmethod
    def from_plan(
        cls, plan: PlanResult, partners: list[Any], duals: dict[str, float] | None = None
    ) -> PlanDecision:
        inp = plan.inputs
        J = len(partners)
        q = np.zeros((J, inp.M))
        cm = plan.extras.get("coman")
        if cm is not None:
            for j, p in enumerate(partners):
                rows = cm.filter(pl.col("coman_id") == p.coman_id).sort("month")
                q[j] = rows["requested_kg"].to_numpy()
        eff = plan.extras.get("d2c_demand_effective")
        shift = eff - inp.d2c_demand if eff is not None else np.zeros_like(inp.d2c_demand)
        sp = plan.extras.get("spend")
        spend = float(sp["recommended_inr"].sum()) if sp is not None else 0.0
        pr: dict[tuple[str, int, int], float] = {}
        if duals:
            for m in range(inp.M):
                for r in range(len(inp.regions)):
                    if f"d2c_r{r}_m{m}" in duals:
                        pr["D2C", r, m] = duals[f"d2c_r{r}_m{m}"]
                for a in range(len(inp.accounts)):
                    if f"b2b_a{a}_m{m}" in duals:
                        pr["B2B", a, m] = duals[f"b2b_a{a}_m{m}"]
        return cls(
            name=plan.mode.name,
            x=plan.x.copy(),
            y=plan.y.copy(),
            coman_q=q,
            coman_cost=np.array([p.unit_cost_inr_per_kg for p in partners]),
            d2c_shift=shift,
            spend=spend,
            priority=pr,
        )


@dataclass
class StressResult:
    name: str
    metrics: dict[str, np.ndarray]  # per-path arrays

    def summary(self) -> dict[str, dict[str, float]]:
        out = {}
        for k, v in self.metrics.items():
            qs = np.quantile(v, QS)
            out[k] = {
                "mean": float(v.mean()),
                "p10": float(qs[0]),
                "p50": float(qs[1]),
                "p90": float(qs[2]),
            }
        return out

    def summary_frame(self) -> pl.DataFrame:
        s = self.summary()
        return pl.DataFrame([{"plan": self.name, "metric": k, **v} for k, v in s.items()])


Line = tuple[str, int]


def _line_value(inp: PlanInputs, weights: tuple[float, float, float], line: Line) -> float:
    w_rev, w_pen, w_gw = weights
    ch, i = line
    if ch == "D2C":
        return w_rev * inp.p_d2c[i] + w_gw * inp.goodwill[i]
    return w_rev * inp.p_b2b[i] + w_pen * inp.penalty[i]


def _ration(
    order: list[Line], want: dict[Line, np.ndarray], got: dict[Line, np.ndarray], supply: np.ndarray
) -> np.ndarray:
    """Give each line (in order) up to want − got; return remaining supply."""
    for li in order:
        g = np.minimum(np.maximum(want[li] - got[li], 0.0), supply)
        got[li] += g
        supply = supply - g
    return supply


def _pro_rata(
    order: list[Line], want: dict[Line, np.ndarray], got: dict[Line, np.ndarray], supply: np.ndarray
) -> np.ndarray:
    """Share supply across lines in proportion to their remaining want (order ignored)."""
    need = {li: np.maximum(want[li] - got[li], 0.0) for li in order}
    total = sum(need.values(), np.zeros_like(supply))
    share = np.minimum(1.0, _ratio(supply, total))
    for li in order:
        got[li] += need[li] * share
    return supply - total * share


def _ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    return np.where(den > 0, num / np.maximum(den, 1e-12), 1.0)


def stress_test(
    decision: PlanDecision,
    scen: Scenarios,
    inp: PlanInputs,
    weights: tuple[float, float, float],
    floor: float,
    unit_cost: float,
    rationing: str = "priority",
) -> StressResult:
    """Replay a fixed plan against every sampled future (vectorized over paths).

    `rationing="pro_rata"` shares scarce supply in proportion to each line's remaining want
    (no contract priority), used by the proportional and first-come-first-served baselines.
    """
    R, A, M, P = len(inp.regions), len(inp.accounts), inp.M, scen.n_paths
    d2c_dem = np.maximum(scen.d2c + decision.d2c_shift[:, None, :], 0.0)  # [R, P, M]
    b2b_dem = scen.b2b  # [A, P, M]
    served_d2c = np.zeros_like(d2c_dem)
    served_b2b = np.zeros_like(b2b_dem)
    waste = np.zeros((P, M))
    carry = np.full(P, inp.initial_inventory)
    coman_delivered = np.zeros((P, M))
    if len(decision.coman_q):
        coman_delivered = (decision.coman_q[:, None, :] * scen.coman_reliability).sum(axis=0)
    lines: list[Line] = [("B2B", a) for a in range(A)] + [("D2C", r) for r in range(R)]

    for m in range(M):
        supply = scen.inhouse[:, m] + coman_delivered[:, m] + carry
        value = {li: decision.priority.get((*li, m), _line_value(inp, weights, li)) for li in lines}
        order = sorted(lines, key=lambda li: (-value[li], li))
        dem: dict[Line, np.ndarray] = {("D2C", r): d2c_dem[r, :, m] for r in range(R)}
        dem |= {("B2B", a): b2b_dem[a, :, m] for a in range(A)}
        ent: dict[Line, np.ndarray] = {("D2C", r): np.full(P, decision.x[r, m]) for r in range(R)}
        ent |= {("B2B", a): np.full(P, decision.y[a, m]) for a in range(A)}
        got = {li: np.zeros(P) for li in lines}

        # 1. B2B floors, pro-rata when supply cannot cover them all
        floors = {("B2B", a): floor * np.minimum(dem["B2B", a], ent["B2B", a]) for a in range(A)}
        total_floor = sum(floors.values(), np.zeros(P))
        share = np.minimum(1.0, _ratio(supply, total_floor))
        for li, f in floors.items():
            got[li] += f * share
            supply = supply - f * share
        # 2. entitlements, then 3. surplus to remaining demand, both in priority order
        split = _ration if rationing == "priority" else _pro_rata
        supply = split(order, {li: np.minimum(dem[li], ent[li]) for li in lines}, got, supply)
        supply = split(order, dem, got, supply)
        carry = np.minimum(supply, inp.carry_limit[m])
        waste[:, m] = supply - carry
        for r in range(R):
            served_d2c[r, :, m] = got["D2C", r]
        for a in range(A):
            served_b2b[a, :, m] = got["B2B", a]

    rev_d2c = (inp.p_d2c[:, None, None] * served_d2c).sum(axis=(0, 2))
    rev_b2b = (inp.p_b2b[:, None, None] * served_b2b).sum(axis=(0, 2))
    short_b2b = np.maximum(b2b_dem - served_b2b, 0.0)
    penalty = (inp.penalty[:, None, None] * short_b2b).sum(axis=(0, 2))
    coman_cost = 0.0
    if len(decision.coman_q):
        coman_cost = float((decision.coman_q * decision.coman_cost[:, None]).sum())
    waste_cost = waste.sum(axis=1) * inp.waste_cost
    production_cost = unit_cost * scen.inhouse.sum(axis=1)
    contribution = (
        rev_d2c + rev_b2b - penalty - waste_cost - coman_cost - decision.spend - production_cost
    )
    shortfall = (short_b2b > 1e-3 * np.maximum(b2b_dem, 1.0)).any(axis=(0, 2))
    metrics = {
        "revenue_inr": rev_d2c + rev_b2b,
        "contribution_inr": contribution,
        "b2b_penalty_inr": penalty,
        "waste_kg": waste.sum(axis=1),
        "d2c_fill_rate": _ratio(served_d2c.sum(axis=(0, 2)), d2c_dem.sum(axis=(0, 2))),
        "b2b_fill_rate": _ratio(served_b2b.sum(axis=(0, 2)), b2b_dem.sum(axis=(0, 2))),
        "any_b2b_shortfall": shortfall.astype(float),
        "served_kg": served_d2c.sum(axis=(0, 2)) + served_b2b.sum(axis=(0, 2)),
    }
    return StressResult(decision.name, metrics)

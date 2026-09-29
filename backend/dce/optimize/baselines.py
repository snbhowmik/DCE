"""Rule-based allocation baselines run through the same stress test (ARCH §5.7, PRD FR-19, T5.8).

All baselines plan on the same inputs as the optimizer (same capacity and demand estimates), spend
the marketing plan, and never activate co-man: they represent the spreadsheet status quo.

- proportional   capacity split pro rata to demand (water-filled to demand); pro-rata on the day
- b2b_first      commitments first, D2C shares the rest proportionally; B2B first on the day
- fcfs           no plan: supply goes pro-rata to realized orders (order-arrival lottery)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from dce.optimize.inputs import PlanInputs
from dce.optimize.stress import PlanDecision, Scenarios, StressResult, stress_test

BASELINES = ("proportional", "b2b_first", "fcfs")


def water_fill(capacity: float, demand: np.ndarray) -> np.ndarray:
    """Allocate `capacity` proportionally to `demand`, never above any line's demand."""
    alloc = np.zeros_like(demand, dtype=float)
    remaining = capacity
    active = demand > 0
    while remaining > 1e-9 and active.any():
        need = demand - alloc
        share = remaining * need * active / need[active].sum()
        take = np.minimum(share, need)
        alloc += take
        remaining -= take.sum()
        active = (demand - alloc) > 1e-9
    return alloc


def baseline_decision(name: str, inp: PlanInputs, planned_spend: float) -> PlanDecision:
    R, A, M = len(inp.regions), len(inp.accounts), inp.M
    x, y = np.zeros((R, M)), np.zeros((A, M))
    carry = inp.initial_inventory
    for m in range(M):
        supply = inp.cap_in[m] + carry
        d2c = inp.d2c_demand[:, m] * inp.d2c_eligible[:, m]
        b2b = inp.commit[:, m] * inp.b2b_eligible[:, m]
        if name == "proportional":
            alloc = water_fill(supply, np.concatenate([d2c, b2b]))
            x[:, m], y[:, m] = alloc[:R], alloc[R:]
        elif name == "b2b_first":
            y[:, m] = water_fill(supply, b2b)
            x[:, m] = water_fill(supply - y[:, m].sum(), d2c)
        elif name == "fcfs":
            pass  # no plan: the day's orders decide
        else:
            raise KeyError(name)
        carry = min(max(supply - x[:, m].sum() - y[:, m].sum(), 0.0), inp.carry_limit[m])
    return PlanDecision(
        name=name,
        x=x,
        y=y,
        coman_q=np.zeros((0, M)),
        coman_cost=np.zeros(0),
        d2c_shift=np.zeros((R, M)),
        spend=planned_spend,
        priority=(
            {("B2B", a, m): 1e9 for a in range(A) for m in range(M)} if name == "b2b_first" else {}
        ),
    )


@dataclass
class Comparison:
    results: dict[str, StressResult]

    def table(self) -> pl.DataFrame:
        return pl.concat([r.summary_frame() for r in self.results.values()])

    def means(self) -> pl.DataFrame:
        return self.table().pivot(on="metric", index="plan", values="mean").sort("plan")


def compare_with_baselines(
    optimizer: StressResult,
    inp: PlanInputs,
    scen: Scenarios,
    weights: tuple[float, float, float],
    floor: float,
    unit_cost: float,
    planned_spend: float,
) -> Comparison:
    results = {optimizer.name: optimizer}
    for name in BASELINES:
        dec = baseline_decision(name, inp, planned_spend)
        rationing = "priority" if name == "b2b_first" else "pro_rata"
        # Rule baselines honour no service floor on the day (except B2B-first, by construction).
        results[name] = stress_test(dec, scen, inp, weights, 0.0, unit_cost, rationing)
    return Comparison(results)

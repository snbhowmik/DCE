"""Co-manufacturing as a MILP extension (ARCH §5.7 constraint 8, T5.4).

Per partner j and month m (a_jm = weeks of month m at/after the earliest possible output):
    q[j,m] ≤ max_j · a_jm · v[j,m]         q[j,m] ≥ min_j · a_jm · v[j,m]
    v[j,m] = 0 where a_jm = 0 (lead time / availability)
    start[j,m] ≥ v[j,m] − v[j,m−1];  Σ_{m'=m}^{m+L−1} v[j,m'] ≥ L_m · start[j,m]
        (min-active linking; L = ⌈min_active_weeks / avg weeks per month⌉, truncated at horizon)
Supply to the capacity balance = reliability_mean_j · q[j,m]; cost c_j · q[j,m].
A partner active in the last history week counts as already activated (no lead time).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl
import pulp

from dce.capacity.coman import CoManPartner
from dce.optimize.inputs import ModeParams, PlanInputs
from dce.optimize.lp import LpModel


@dataclass
class CoManInputs:
    partners: list[CoManPartner]
    decision_week: date
    already_active: set[str] = field(default_factory=set)
    forced_from: dict[str, int] = field(default_factory=dict)  # mitigation: force active from m

    def available_weeks(self, p: CoManPartner, inp: PlanInputs) -> np.ndarray:
        first = (
            self.decision_week
            if p.coman_id in self.already_active
            else p.earliest_output(self.decision_week)
        )
        return np.array([sum(w >= first for w in m.weeks) for m in inp.months], dtype=float)


@dataclass
class CoManVars:
    q: dict[tuple[int, int], pulp.LpVariable] = field(default_factory=dict)
    v: dict[tuple[int, int], pulp.LpVariable] = field(default_factory=dict)
    avail: dict[int, np.ndarray] = field(default_factory=dict)


def add_coman(model: LpModel, inp: PlanInputs, cin: CoManInputs, mode: ModeParams) -> CoManVars:
    prob = model.prob
    M = inp.M
    avg_wk = float(np.mean([len(m.weeks) for m in inp.months]))
    cv = CoManVars()
    for j, p in enumerate(cin.partners):
        a = cin.available_weeks(p, inp)
        cv.avail[j] = a
        L = max(1, math.ceil(p.min_active_weeks / avg_wk))
        for m in range(M):
            v = pulp.LpVariable(f"cmv_{j}_{m}", cat="Binary")
            q = pulp.LpVariable(f"cmq_{j}_{m}", 0)
            cv.v[j, m], cv.q[j, m] = v, q
            prob += (q <= p.max_kg_per_week * a[m] * v), f"coman_max_j{j}_m{m}"
            prob += (q >= p.min_kg_per_week * a[m] * v), f"coman_min_j{j}_m{m}"
            if a[m] == 0:
                prob += (v == 0), f"coman_leadtime_j{j}_m{m}"
            if p.coman_id in cin.forced_from and m >= cin.forced_from[p.coman_id] and a[m] > 0:
                prob += (v == 1), f"coman_forced_j{j}_m{m}"
            model.supply_extra[m] = model.supply_extra.get(m, 0) + p.reliability_mean * q
            model.objective_terms.append(-p.unit_cost_inr_per_kg * q)
        for m in range(M):
            prev = cv.v[j, m - 1] if m > 0 else (1 if p.coman_id in cin.already_active else 0)
            start = pulp.LpVariable(f"cms_{j}_{m}", 0, 1)
            prob += (start >= cv.v[j, m] - prev), f"coman_start_j{j}_m{m}"
            window = [cv.v[j, mm] for mm in range(m, min(M, m + L))]
            prob += (pulp.lpSum(window) >= len(window) * start), f"coman_minrun_j{j}_m{m}"
    return cv


def coman_table(cv: CoManVars, inp: PlanInputs, cin: CoManInputs) -> pl.DataFrame:
    rows = []
    for j, p in enumerate(cin.partners):
        for m in range(inp.M):
            q = cv.q[j, m].value() or 0.0
            rows.append(
                {
                    "coman_id": p.coman_id,
                    "month": m,
                    "active": bool(round(cv.v[j, m].value() or 0.0)),
                    "requested_kg": float(q),
                    "expected_delivered_kg": float(q * p.reliability_mean),
                    "available_weeks": int(cv.avail[j][m]),
                    "cost_inr": float(q * p.unit_cost_inr_per_kg),
                }
            )
    return pl.DataFrame(rows)


def already_active_partners(activity: pl.DataFrame, last_week: date) -> set[str]:
    """Partners with requested volume in the last history week."""
    recent = activity.filter((pl.col("week_start") == last_week) & (pl.col("requested_kg") > 0))
    return set(recent["coman_id"].to_list())

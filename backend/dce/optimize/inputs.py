"""Optimizer inputs assembled from forecasts, capacity and the contract tables (ARCH §5.7).

Monthly quantities are quantiles of *monthly sums of paths* (not sums of weekly quantiles), so a
month's P15 capacity is the 15th percentile of that month's total output.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from dce.capacity.model import CapacityForecast
from dce.forecast.run import DatasetForecast


@dataclass(frozen=True)
class Month:
    idx: int
    weeks: tuple[date, ...]

    @property
    def start(self) -> date:
        return self.weeks[0]

    @property
    def end(self) -> date:
        return self.weeks[-1] + timedelta(days=6)


def split_months(horizon: list[date], month_weeks: list[int]) -> list[Month]:
    if sum(month_weeks) != len(horizon):
        raise ValueError(f"month_weeks {month_weeks} must sum to horizon {len(horizon)}")
    out, i = [], 0
    for m, n in enumerate(month_weeks):
        out.append(Month(m, tuple(horizon[i : i + n])))
        i += n
    return out


@dataclass(frozen=True)
class ModeParams:
    """The strategy levers the optimizer uses (built from validated YAML in T5.2)."""

    name: str
    q_capacity: float = 0.5
    q_demand_b2b: float = 0.5
    q_demand_d2c: float = 0.5
    w_rev: float = 1.0
    w_pen: float = 1.0
    w_gw: float = 0.3
    w_spend: float = 1.0
    w_reach: float = 0.0
    w_cust: float = 0.0
    b2b_service_floor: float = 0.9
    res_gate: float = 0.0
    exploration_share: float = 0.0
    breach_threshold: float = 0.3

    @classmethod
    def from_mode_config(cls, name: str, cfg: dict[str, Any], opt: dict[str, Any]) -> ModeParams:
        w = cfg.get("weights", {})
        return cls(
            name=name,
            q_capacity=float(cfg.get("q_capacity", 0.5)),
            q_demand_b2b=float(cfg.get("q_demand_b2b", 0.5)),
            q_demand_d2c=float(cfg.get("q_demand_d2c", opt.get("q_demand_d2c", 0.5))),
            w_rev=float(w.get("rev", 1.0)),
            w_pen=float(w.get("pen", 1.0)),
            w_gw=float(w.get("gw", 0.3)),
            w_spend=float(w.get("spend", 1.0)),
            w_reach=float(w.get("reach", 0.0)),
            w_cust=float(w.get("cust", 0.0)),
            b2b_service_floor=float(cfg.get("b2b_service_floor", 0.9)),
            res_gate=float(cfg.get("res_gate", 0.0)),
            exploration_share=float(cfg.get("exploration_share", 0.0)),
            breach_threshold=float(cfg.get("breach_threshold", 0.3)),
        )


@dataclass
class PlanInputs:
    months: list[Month]
    regions: list[str]
    accounts: list[str]
    account_region: list[str]
    cap_in: np.ndarray  # [M] kg
    carry_limit: np.ndarray  # [M] kg
    d2c_demand: np.ndarray  # [R, M] kg
    commit: np.ndarray  # [A, M] kg (forecast orders at q_demand_b2b)
    p_d2c: np.ndarray  # [R] ₹/kg
    p_b2b: np.ndarray  # [A] ₹/kg
    penalty: np.ndarray  # [A] ₹/kg short
    goodwill: np.ndarray  # [R] ₹/kg unmet
    reach: np.ndarray  # [A] in [0, 1]
    waste_cost: float
    d2c_eligible: np.ndarray  # [R, M] bool
    b2b_eligible: np.ndarray  # [A, M] bool
    concentration_cap: float
    floor_penalty: float
    initial_inventory: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def M(self) -> int:
        return len(self.months)

    def with_capacity(self, cap_in: np.ndarray) -> PlanInputs:
        """Copy with different capacity (carry limit scales with it)."""
        ratio = np.divide(cap_in, self.cap_in, out=np.ones_like(cap_in), where=self.cap_in > 0)
        return replace(self, cap_in=np.asarray(cap_in, float), carry_limit=self.carry_limit * ratio)


def monthly_quantile(
    paths: np.ndarray, months: list[Month], horizon: list[date], q: float
) -> np.ndarray:
    """paths [..., P, H] → [..., M] quantile over paths of monthly sums."""
    idx = {w: i for i, w in enumerate(horizon)}
    sums = np.stack(
        [paths[..., [idx[w] for w in m.weeks]].sum(axis=-1) for m in months], axis=-1
    )  # [..., P, M]
    return np.quantile(sums, q, axis=-2)


def _eligible(
    matrix: pl.DataFrame, sku: str, channel: str, region: str, months: list[Month]
) -> np.ndarray:
    rows = matrix.filter(
        (pl.col("sku_id") == sku) & (pl.col("channel") == channel) & (pl.col("region_id") == region)
    )
    out = np.zeros(len(months), dtype=bool)
    for frm, to in rows.select("eligible_from", "eligible_to").iter_rows():
        for m in months:
            if frm <= m.end and (to is None or to >= m.start):
                out[m.idx] = True
    return out


def build_inputs(
    tables: dict[str, pl.DataFrame],
    forecast: DatasetForecast,
    capacity: CapacityForecast,
    mode: ModeParams,
    opt: dict[str, Any],
) -> PlanInputs:
    horizon = forecast.horizon
    months = split_months(horizon, list(opt.get("month_weeks", [4, 4, 5])))
    sku = str(
        tables["skus"]
        .filter(
            (pl.col("status") == "production") & (pl.col("product_line") == capacity.product_line)
        )
        .sort("sku_id")["sku_id"][0]
    )
    sids, dem_paths = forecast.paths()
    series = forecast.series

    # D2C: aggregate series per region.
    d2c = series.filter(pl.col("channel") == "D2C")
    regions = sorted(d2c["region_id"].unique().to_list())
    region_paths = np.zeros((len(regions), *dem_paths.shape[1:]))
    for sid, reg in d2c.select("series_id", "region_id").iter_rows():
        region_paths[regions.index(reg)] += dem_paths[sids.index(sid)]
    d2c_demand = monthly_quantile(region_paths, months, horizon, mode.q_demand_d2c)

    # B2B: one row per forecast account.
    b2b = series.filter(pl.col("channel") == "B2B").sort("account_id")
    accounts = b2b["account_id"].to_list()
    account_region = b2b["region_id"].to_list()
    acc_paths = (
        np.stack([dem_paths[sids.index(s)] for s in b2b["series_id"]])
        if accounts
        else np.zeros((0, *dem_paths.shape[1:]))
    )
    commit = (
        monthly_quantile(acc_paths, months, horizon, mode.q_demand_b2b)
        if accounts
        else np.zeros((0, len(months)))
    )

    cap_in = monthly_quantile(capacity.paths, months, horizon, mode.q_capacity)
    weeks_per_month = np.array([len(m.weeks) for m in months], dtype=float)
    carry_limit = cap_in / weeks_per_month * capacity.perishability.carryover_weeks

    price = float(tables["skus"].filter(pl.col("sku_id") == sku)["list_price_d2c_inr_per_kg"][0])
    p_d2c = np.full(len(regions), price)
    goodwill = p_d2c * float(opt.get("goodwill_fraction_of_price", 0.25))
    acct = tables["b2b_accounts"].filter(pl.col("account_id").is_in(accounts)).sort("account_id")
    p_b2b = acct["contract_price_inr_per_kg"].fill_null(price).to_numpy().astype(float)
    penalty = acct["shortfall_penalty_inr_per_kg"].fill_null(0.0).to_numpy().astype(float)
    raw_reach = (
        np.log1p(acct["outlets_count"].to_numpy().astype(float))
        * acct["regions_served"].str.split("|").list.len().to_numpy()
        if accounts
        else np.zeros(0)
    )
    reach = raw_reach / raw_reach.max() if raw_reach.size and raw_reach.max() > 0 else raw_reach

    cold = dict(tables["regions"].select("region_id", "cold_chain_available").iter_rows())
    pm = tables["product_matrix"]
    d2c_elig = np.array(
        [_eligible(pm, sku, "D2C", r, months) & bool(cold.get(r, False)) for r in regions]
    ).reshape(len(regions), len(months))
    b2b_elig = np.array([_eligible(pm, sku, "B2B", r, months) for r in account_region]).reshape(
        len(accounts), len(months)
    )

    return PlanInputs(
        months=months,
        regions=regions,
        accounts=accounts,
        account_region=account_region,
        cap_in=cap_in,
        carry_limit=carry_limit,
        d2c_demand=d2c_demand,
        commit=commit,
        p_d2c=p_d2c,
        p_b2b=p_b2b,
        penalty=penalty,
        goodwill=goodwill,
        reach=reach,
        waste_cost=capacity.perishability.waste_cost_inr_per_kg,
        d2c_eligible=d2c_elig,
        b2b_eligible=b2b_elig,
        concentration_cap=float(opt.get("concentration_cap", 0.30)),
        floor_penalty=float(opt.get("floor_penalty_inr_per_kg", 1e6)),
        initial_inventory=float(opt.get("initial_inventory_kg", 0.0)),
        meta={"sku_id": sku, "mode": mode.name, "product_line": capacity.product_line},
    )

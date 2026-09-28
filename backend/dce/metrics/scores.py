"""Compute RES and AQS for a loaded dataset (the entry point other modules use)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from dce.demand.reconstruct import weekly_demand
from dce.metrics.aqs import AqsConfig, account_quality
from dce.metrics.cohorts import LtvConfig, cohort_ltv, cohort_table, region_ltv
from dce.metrics.funnel import funnel_metrics
from dce.metrics.res import ResConfig, response_evidence


@dataclass
class EvidenceScores:
    res: pl.DataFrame
    aqs: pl.DataFrame
    funnel_monthly: pl.DataFrame
    region_ltv: pl.DataFrame


def last_complete_month(last_week: date) -> date:
    """Month start of the last month fully covered by history ending on `last_week` + 6 days."""
    end = date.fromordinal(last_week.toordinal() + 6)
    nxt = date(end.year + (end.month == 12), end.month % 12 + 1, 1)
    if (nxt - end).days == 1:
        return end.replace(day=1)
    prev = end.replace(day=1)
    return date(prev.year - (prev.month == 1), (prev.month - 2) % 12 + 1, 1)


def evidence_scores(
    tables: dict[str, pl.DataFrame],
    window: tuple[date, date],
    scoring: dict[str, Any],
    aqs_profile: str = "balanced",
) -> EvidenceScores:
    first, last = window
    demand = weekly_demand(tables["orders"], last)
    fm = funnel_metrics(tables, first, last, "month")
    ltv_cfg = LtvConfig.from_scoring(scoring)
    rltv = region_ltv(cohort_ltv(cohort_table(tables, last_complete_month(last)), ltv_cfg), ltv_cfg)
    res = response_evidence(
        regions=tables["regions"],
        demand=demand,
        marketing_daily=tables["marketing_daily"],
        funnel_monthly=fm,
        region_ltv=rltv,
        orders=tables["orders"],
        last_week=last,
        cfg=ResConfig.from_scoring(scoring),
    )
    aqs = account_quality(
        accounts=tables["b2b_accounts"],
        orders=tables["orders"],
        skus=tables["skus"],
        capacity_plan=tables["capacity_plan"],
        last_week=last,
        profile=aqs_profile,
        cfg=AqsConfig.from_scoring(scoring),
    )
    return EvidenceScores(res=res, aqs=aqs, funnel_monthly=fm, region_ltv=rltv)

"""Continuity and coverage checks beyond the per-table contract (PRD FR-1)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast

import polars as pl

from dce.contract.spec import MIN_HISTORY_WEEKS
from dce.contract.validate import Issue
from dce.numerics import SUM_DECIMALS

FORWARD_WEEKS = 13
_SAMPLE = 5


def dmin(s: pl.Series) -> date:
    return cast(date, s.min())


def dmax(s: pl.Series) -> date:
    return cast(date, s.max())


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def history_window(
    tables: dict[str, pl.DataFrame], manifest: dict[str, Any] | None
) -> tuple[date, date] | None:
    """(first Monday, last Monday) of history: from the manifest, else from orders."""
    try:
        start = date.fromisoformat((manifest or {})["history_start"])
        end = date.fromisoformat((manifest or {})["history_end"])
    except (KeyError, TypeError, ValueError):
        orders = tables.get("orders")
        if orders is None or orders.is_empty():
            return None
        start, end = dmin(orders["order_date"]), dmax(orders["order_date"])
    return monday(start), monday(end)


def _weeks(first: date, last: date) -> list[date]:
    return [first + timedelta(weeks=i) for i in range((last - first).days // 7 + 1)]


def _missing_weeks(dates: pl.Series, first: date, last: date) -> list[date]:
    seen = set(dates.dt.truncate("1w").unique().to_list())
    return [w for w in _weeks(first, last) if w not in seen]


def continuity_issues(
    tables: dict[str, pl.DataFrame], manifest: dict[str, Any] | None
) -> list[Issue]:
    issues: list[Issue] = []
    window = history_window(tables, manifest)
    if window is None:
        return [Issue("orders", None, "coverage", "error", "cannot determine history window")]
    first, last = window
    n_weeks = (last - first).days // 7 + 1
    if n_weeks < MIN_HISTORY_WEEKS:
        issues.append(
            Issue(
                None,
                None,
                "min_history",
                "error",
                f"history has {n_weeks} weeks; contract minimum is {MIN_HISTORY_WEEKS}",
            )
        )

    history_end = last + timedelta(days=6)
    for name, col in (
        ("orders", "order_date"),
        ("marketing_daily", "date"),
        ("nps_responses", "response_date"),
        ("coman_activity", "week_start"),
    ):
        df = tables.get(name)
        if df is None or df.is_empty():
            continue
        out = df.filter((pl.col(col) < first) | (pl.col(col) > history_end))
        if out.height:
            issues.append(
                Issue(
                    name,
                    col,
                    "out_of_range",
                    "warning",
                    f"{out.height} row(s) dated outside history {first}..{history_end}",
                    out.height,
                )
            )

    orders = tables.get("orders")
    if orders is not None and not orders.is_empty():
        for (channel, region), grp in orders.group_by(
            ["channel", "region_id"], maintain_order=True
        ):
            d = grp["order_date"]
            lo, hi = monday(dmin(d)), monday(dmax(d))
            gaps = _missing_weeks(d, lo, hi)
            if gaps:
                issues.append(
                    Issue(
                        "orders",
                        None,
                        "missing_periods",
                        "warning",
                        f"{channel}/{region}: {len(gaps)} week(s) with no orders between "
                        f"{lo} and {hi}, e.g. {[str(g) for g in gaps[:_SAMPLE]]}",
                        len(gaps),
                    )
                )

    mkt = tables.get("marketing_daily")
    if mkt is not None and not mkt.is_empty():
        for (region,), grp in mkt.group_by(["region_id"], maintain_order=True):
            days = grp["date"].unique()
            span = (dmax(days) - dmin(days)).days + 1
            if days.len() < span:
                issues.append(
                    Issue(
                        "marketing_daily",
                        "date",
                        "missing_periods",
                        "warning",
                        f"{region}: {span - days.len()} day(s) without marketing rows",
                        span - days.len(),
                    )
                )

    horizon_end = last + timedelta(weeks=FORWARD_WEEKS)
    plan = tables.get("capacity_plan")
    if plan is not None:
        gaps = _missing_weeks(plan["week_start"], first, horizon_end)
        if gaps:
            issues.append(
                Issue(
                    "capacity_plan",
                    "week_start",
                    "plan_coverage",
                    "error",
                    f"{len(gaps)} week(s) missing from {first} to {horizon_end} "
                    f"(history + {FORWARD_WEEKS} ahead), e.g. {[str(g) for g in gaps[:_SAMPLE]]}",
                    len(gaps),
                )
            )

    mplan = tables.get("marketing_plan")
    if mplan is not None:
        latest = dmax(mplan["week_start"]) if not mplan.is_empty() else None
        if latest is None or latest < horizon_end:
            issues.append(
                Issue(
                    "marketing_plan",
                    "week_start",
                    "plan_coverage",
                    "warning",
                    f"planned spend ends {latest}; forecasts need it through {horizon_end}",
                )
            )

    issues += spend_plan_divergence(tables, first, last)

    accts = tables.get("b2b_accounts")
    if accts is not None:
        bad = accts.filter(
            (pl.col("status") == "active") & pl.col("committed_kg_per_month").is_null()
        )
        if bad.height:
            issues.append(
                Issue(
                    "b2b_accounts",
                    "committed_kg_per_month",
                    "active_without_commitment",
                    "warning",
                    f"{bad.height} active account(s) without a commitment: "
                    f"{bad['account_id'].head(_SAMPLE).to_list()}",
                    bad.height,
                )
            )
    return issues


def table_summary(tables: dict[str, pl.DataFrame]) -> dict[str, dict[str, Any]]:
    date_cols = {
        "orders": "order_date",
        "capacity_batches": "start_date",
        "capacity_plan": "week_start",
        "coman_activity": "week_start",
        "marketing_daily": "date",
        "marketing_plan": "week_start",
        "customers": "acquired_date",
        "nps_responses": "response_date",
    }
    out: dict[str, dict[str, Any]] = {}
    for name, df in tables.items():
        entry: dict[str, Any] = {"rows": df.height}
        col = date_cols.get(name)
        if col and not df.is_empty():
            entry["date_min"] = str(df[col].min())
            entry["date_max"] = str(df[col].max())
        out[name] = entry
    return out


def spend_plan_divergence(
    tables: dict[str, pl.DataFrame], first: date, last: date, share: float = 0.10
) -> list[Issue]:
    """Warn when planned D2C spend is positive but realized spend is zero in many history weeks.

    Realized spend drives the response model and funnel metrics; planned spend is a forecast
    covariate. If they diverge wholesale, both are suspect.
    """
    mkt, plan = tables.get("marketing_daily"), tables.get("marketing_plan")
    if mkt is None or plan is None:
        return []
    realized = (
        mkt.filter(pl.col("channel") == "D2C")
        .with_columns(pl.col("date").dt.truncate("1w").alias("week_start"))
        .group_by("week_start")
        .agg(pl.col("spend_inr").sum().round(SUM_DECIMALS).alias("realized"))
    )
    planned = (
        plan.filter((pl.col("channel") == "D2C") & pl.col("week_start").is_between(first, last))
        .group_by("week_start")
        .agg(pl.col("planned_spend_inr").sum().round(SUM_DECIMALS).alias("planned"))
    )
    j = planned.join(realized, on="week_start", how="left").with_columns(
        pl.col("realized").fill_null(0.0)
    )
    bad = j.filter((pl.col("planned") > 0) & (pl.col("realized") <= 0))
    n_weeks = (last - first).days // 7 + 1
    if bad.height <= share * n_weeks:
        return []
    last_spend = dmax(realized.filter(pl.col("realized") > 0)["week_start"])
    return [
        Issue(
            "marketing_daily",
            "spend_inr",
            "spend_plan_divergence",
            "warning",
            f"{bad.height} of {n_weeks} history weeks have planned D2C spend but zero realized "
            f"spend (last realized spend: week of {last_spend}); response model and funnel "
            "metrics will be low-confidence",
            bad.height,
        )
    ]

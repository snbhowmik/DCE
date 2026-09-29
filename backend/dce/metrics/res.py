"""Response Evidence Score per region × channel (IDEATION §9.1, ARCH §5.3).

RES_raw    = Σ_c w_c · z(component_c)            z within channel, across regions; missing → 0
RES        = conf · RES_raw + (1 − conf) · mean(RES_raw)   conf = n_eff / (n_eff + k)

Components
  lift       mean over spend step-ups of (late lift ÷ early lift), relative to the pre-step level:
             ≈1 sustained response, ≈0 a spike that died
  econ       LTV:CAC over the trailing window (D2C LTV from cohorts; B2B from contracts)
  retention  mean of (1 − monthly churn) and repeat-purchase rate (D2C) over the window
  nps        NPS over the window
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

from dce.numerics import SUM_DECIMALS

COMPONENTS = ("lift", "econ", "retention", "nps")


@dataclass(frozen=True)
class StepUpConfig:
    min_increase: float = 0.20
    sustain_weeks: int = 2
    pre_weeks: int = 4
    early_weeks: tuple[int, int] = (1, 4)
    late_weeks: tuple[int, int] = (5, 10)
    ratio_clip: tuple[float, float] = (0.0, 1.5)


@dataclass(frozen=True)
class ResConfig:
    window_weeks: int = 52
    weights: dict[str, float] = field(
        default_factory=lambda: {"lift": 0.35, "econ": 0.30, "retention": 0.20, "nps": 0.15}
    )
    shrinkage_k: dict[str, float] = field(default_factory=lambda: {"D2C": 100.0, "B2B": 3.0})
    step_up: StepUpConfig = field(default_factory=StepUpConfig)

    @classmethod
    def from_scoring(cls, scoring: dict[str, Any]) -> ResConfig:
        r = scoring.get("res", {})
        su = r.get("step_up", {})
        d = cls()
        return cls(
            window_weeks=int(r.get("window_weeks", d.window_weeks)),
            weights={k: float(v) for k, v in r.get("weights", d.weights).items()},
            shrinkage_k={k: float(v) for k, v in r.get("shrinkage_k", d.shrinkage_k).items()},
            step_up=StepUpConfig(
                min_increase=float(su.get("min_increase", d.step_up.min_increase)),
                sustain_weeks=int(su.get("sustain_weeks", d.step_up.sustain_weeks)),
                pre_weeks=int(su.get("pre_weeks", d.step_up.pre_weeks)),
                early_weeks=tuple(su.get("early_weeks", d.step_up.early_weeks)),
                late_weeks=tuple(su.get("late_weeks", d.step_up.late_weeks)),
                ratio_clip=tuple(su.get("ratio_clip", d.step_up.ratio_clip)),
            ),
        )


@dataclass(frozen=True)
class LiftEvent:
    week_index: int
    pre_spend: float
    step_spend: float
    early_lift: float
    late_lift: float
    ratio: float | None


def step_up_events(spend: np.ndarray, cfg: StepUpConfig) -> list[int]:
    """Indices t where spend rises ≥ min_increase above the pre-period mean and stays there."""
    events: list[int] = []
    last_needed = cfg.late_weeks[1]
    t = cfg.pre_weeks
    while t + last_needed < len(spend):
        pre = float(spend[t - cfg.pre_weeks : t].mean())
        level = pre * (1 + cfg.min_increase)
        window = spend[t : t + cfg.sustain_weeks]
        if (window > pre).all() and (window >= level).all():
            events.append(t)
            t += last_needed + 1  # no overlapping events
        else:
            t += 1
    return events


def sustained_lift(
    spend: np.ndarray, demand: np.ndarray, cfg: StepUpConfig
) -> tuple[float | None, list[LiftEvent]]:
    """Mean late/early lift ratio across informative step-ups (early lift > 0)."""
    events = []
    for t in step_up_events(spend, cfg):
        base = float(demand[t - cfg.pre_weeks : t].mean())
        e0, e1 = cfg.early_weeks
        l0, l1 = cfg.late_weeks
        early = float(demand[t + e0 : t + e1 + 1].mean()) - base
        late = float(demand[t + l0 : t + l1 + 1].mean()) - base
        ratio = float(np.clip(late / early, *cfg.ratio_clip)) if early > 0 else None
        events.append(
            LiftEvent(
                t, float(spend[t - cfg.pre_weeks : t].mean()), float(spend[t]), early, late, ratio
            )
        )
    ratios = [e.ratio for e in events if e.ratio is not None]
    return (float(np.mean(ratios)) if ratios else None), events


def zscore_within(col: str, by: str) -> pl.Expr:
    """z within group; null value → 0; zero/undefined std → 0."""
    mean = pl.col(col).mean().over(by)
    std = pl.col(col).std(ddof=0).over(by)
    z = pl.when(std > 0).then((pl.col(col) - mean) / std).otherwise(0.0)
    return pl.when(pl.col(col).is_null()).then(0.0).otherwise(z).fill_null(0.0)


def shrink(res_raw: pl.Expr, conf: pl.Expr, by: str) -> pl.Expr:
    return conf * res_raw + (1 - conf) * res_raw.mean().over(by)


def _window_start(last_week: date, weeks: int) -> date:
    return last_week - timedelta(weeks=weeks - 1)


def lift_components(
    demand: pl.DataFrame, marketing_daily: pl.DataFrame, cfg: StepUpConfig
) -> pl.DataFrame:
    """Sustained-lift per region × channel from weekly spend and weekly demand."""
    wk_spend = (
        marketing_daily.with_columns(pl.col("date").dt.truncate("1w").alias("week_start"))
        .group_by("region_id", "channel", "week_start")
        .agg(pl.col("spend_inr").sum().round(SUM_DECIMALS).alias("spend"))
    )
    wk_demand = demand.group_by("region_id", "channel", "week_start").agg(
        pl.col("demand_kg").sum().round(SUM_DECIMALS).alias("demand")
    )
    rows = []
    for (region, channel), g in wk_demand.group_by(["region_id", "channel"], maintain_order=True):
        g = (
            g.join(wk_spend, on=["region_id", "channel", "week_start"], how="left")
            .with_columns(pl.col("spend").fill_null(0.0))
            .sort("week_start")
        )
        value, events = sustained_lift(g["spend"].to_numpy(), g["demand"].to_numpy(), cfg)
        rows.append(
            {
                "region_id": region,
                "channel": channel,
                "lift": value,
                "n_lift_events": len(events),
                "n_informative_events": sum(e.ratio is not None for e in events),
            }
        )
    schema = {
        "region_id": pl.String,
        "channel": pl.String,
        "lift": pl.Float64,
        "n_lift_events": pl.Int64,
        "n_informative_events": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def response_evidence(
    *,
    regions: pl.DataFrame,
    demand: pl.DataFrame,
    marketing_daily: pl.DataFrame,
    funnel_monthly: pl.DataFrame,
    region_ltv: pl.DataFrame,
    orders: pl.DataFrame,
    last_week: date,
    cfg: ResConfig | None = None,
) -> pl.DataFrame:
    """RES per region × channel with raw components, z-scores, confidence and shrunk score."""
    cfg = cfg or ResConfig()
    start = _window_start(last_week, cfg.window_weeks)
    month0 = start.replace(day=1)
    fm = funnel_monthly.filter(pl.col("period_start") >= month0).sort("period_start")

    agg = fm.group_by("region_id", "channel").agg(
        pl.col("spend_inr").sum().round(SUM_DECIMALS).alias("spend"),
        pl.col("new_customers").sum().alias("new_customers"),
        pl.col("customers_lost").sum().alias("lost"),
        pl.col("customers_at_start").sum().alias("at_start"),
        pl.col("promoters").sum(),
        pl.col("detractors").sum(),
        pl.col("nps_responses").sum(),
        pl.col("ltv_inr").filter(pl.col("channel") == "B2B").mean().alias("b2b_ltv"),
        pl.col("active_customers").last().alias("active_now"),
    )
    repeat = (
        orders.filter(
            (pl.col("channel") == "D2C")
            & (pl.col("fulfilled_qty_kg") > 0)
            & (pl.col("order_date") >= start)
        )
        .group_by("region_id", "customer_id")
        .len()
        .group_by("region_id")
        .agg((pl.col("len") >= 2).mean().alias("repeat_rate"))
        .with_columns(pl.lit("D2C").alias("channel"))
    )

    def div(a: pl.Expr, b: pl.Expr) -> pl.Expr:
        return pl.when(b > 0).then(a / b).otherwise(None)

    grid = regions.select("region_id").join(pl.DataFrame({"channel": ["D2C", "B2B"]}), how="cross")
    df = (
        grid.join(agg, on=["region_id", "channel"], how="left")
        .join(region_ltv.select("region_id", "ltv_inr"), on="region_id", how="left")
        .join(repeat, on=["region_id", "channel"], how="left")
        .join(
            lift_components(demand, marketing_daily, cfg.step_up),
            on=["region_id", "channel"],
            how="left",
        )
        .with_columns(
            div(pl.col("spend"), pl.col("new_customers")).alias("cac_inr"),
            pl.when(pl.col("channel") == "D2C")
            .then(pl.col("ltv_inr"))
            .otherwise(pl.col("b2b_ltv"))
            .alias("ltv_window_inr"),
            (1 - div(pl.col("lost"), pl.col("at_start"))).alias("one_minus_churn"),
            (100 * div(pl.col("promoters") - pl.col("detractors"), pl.col("nps_responses"))).alias(
                "nps"
            ),
        )
        .with_columns(
            div(pl.col("ltv_window_inr"), pl.col("cac_inr")).alias("econ"),
            pl.mean_horizontal("one_minus_churn", "repeat_rate").alias("retention"),
            pl.when(pl.col("channel") == "D2C")
            .then(pl.col("new_customers"))
            .otherwise(pl.col("active_now"))
            .fill_null(0)
            .cast(pl.Float64)
            .alias("n_eff"),
        )
    )  # fmt: skip
    df = df.with_columns([zscore_within(c, "channel").alias(f"z_{c}") for c in COMPONENTS])
    k = pl.col("channel").replace_strict(cfg.shrinkage_k, default=100.0, return_dtype=pl.Float64)
    res_raw = pl.sum_horizontal([cfg.weights[c] * pl.col(f"z_{c}") for c in COMPONENTS])
    conf = pl.col("n_eff") / (pl.col("n_eff") + k)
    df = df.with_columns(res_raw.alias("res_raw"), conf.alias("confidence")).with_columns(
        shrink(pl.col("res_raw"), pl.col("confidence"), "channel").alias("res"),
        pl.concat_list([pl.when(pl.col(c).is_null()).then(pl.lit(c)) for c in COMPONENTS])
        .list.drop_nulls()
        .alias("missing_components"),
    )
    return df.select(
        "region_id", "channel", "res", "res_raw", "confidence", "n_eff",
        *COMPONENTS, *(f"z_{c}" for c in COMPONENTS), "missing_components",
        "n_lift_events", "n_informative_events", "cac_inr", "ltv_window_inr",
        "one_minus_churn", "repeat_rate",
    ).sort("channel", "region_id")  # fmt: skip

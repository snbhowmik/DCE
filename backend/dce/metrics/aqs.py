"""Account Quality Score per B2B account / candidate (IDEATION §9.2, ARCH §5.3).

AQS = Σ w_c · z(c)  for c in volume, stability, reach, margin, reliability
      − w_penalty · z(penalty exposure) − w_concentration · (capacity share ÷ cap)

z is taken against accounts with contract history (reference population). Pipeline accounts
have no history: stability, reliability, margin and penalty come from the mean of the same
account type in the reference population (global mean if none) and are flagged `prior=true`.
The weight profile is chosen by the caller (strategy mode); this module never reads modes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

POSITIVE = ("volume", "stability", "reach", "margin", "reliability")
PRIOR_COMPONENTS = ("stability", "reliability", "margin", "penalty")
AVG_WEEKS_PER_MONTH = 52 / 12


@dataclass(frozen=True)
class AqsConfig:
    concentration_cap: float = 0.30
    shrinkage_k_months: float = 6.0
    profiles: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "balanced": {
                "volume": 0.2, "stability": 0.2, "reach": 0.15, "margin": 0.2,
                "reliability": 0.15, "penalty": 0.1, "concentration": 0.5,
            }
        }
    )  # fmt: skip

    @classmethod
    def from_scoring(cls, scoring: dict[str, Any]) -> AqsConfig:
        a = scoring.get("aqs", {})
        d = cls()
        return cls(
            concentration_cap=float(a.get("concentration_cap", d.concentration_cap)),
            shrinkage_k_months=float(a.get("shrinkage_k_months", d.shrinkage_k_months)),
            profiles={
                name: {k: float(v) for k, v in w.items()}
                for name, w in a.get("profiles", d.profiles).items()
            },
        )


def monthly_capacity_kg(capacity_plan: pl.DataFrame, last_week: date, weeks: int = 13) -> float:
    """Planned in-house kg per month, from the last `weeks` weeks of history in the plan."""
    recent = capacity_plan.filter(
        pl.col("week_start").is_between(last_week - timedelta(weeks=weeks - 1), last_week)
    )
    if recent.is_empty():
        return float("nan")
    weekly = (
        recent.with_columns(
            (pl.col("planned_batches") * pl.col("planned_yield_per_batch_kg")).alias("kg")
        )
        .group_by("week_start")
        .agg(pl.col("kg").sum())["kg"]
        .mean()
    )
    return float(weekly) * AVG_WEEKS_PER_MONTH  # type: ignore[arg-type]


def _history_components(
    accounts: pl.DataFrame, orders: pl.DataFrame, last_week: date
) -> pl.DataFrame:
    """Per account with a contract: stability, reliability, months of history."""
    hist_end = last_week + timedelta(days=6)
    acc = accounts.filter(pl.col("contract_start").is_not_null()).select(
        "account_id",
        "contract_start",
        pl.min_horizontal(pl.col("contract_end").fill_null(hist_end), pl.lit(hist_end)).alias(
            "active_end"
        ),
        "committed_kg_per_month",
    )
    o = orders.filter(pl.col("channel") == "B2B").join(acc, on="account_id")
    o = o.filter(pl.col("order_date").is_between(pl.col("contract_start"), pl.col("active_end")))
    monthly = (
        o.with_columns(pl.col("order_date").dt.truncate("1mo").alias("m"))
        .group_by("account_id", "m")
        .agg(pl.col("requested_qty_kg").sum().alias("kg"), pl.col("committed_kg_per_month").first())
        .with_columns((pl.col("kg") / pl.col("committed_kg_per_month")).alias("ratio"))
    )
    stab = monthly.group_by("account_id").agg(
        (1 - pl.col("ratio").std(ddof=0) / pl.col("ratio").mean()).alias("stability"),
        pl.len().alias("months_history"),
    )
    weeks_active = acc.with_columns(
        (((pl.col("active_end") - pl.col("contract_start")).dt.total_days() // 7) + 1).alias(
            "weeks_active"
        )
    ).select("account_id", "weeks_active")
    weeks_ordered = (
        o.with_columns(pl.col("order_date").dt.truncate("1w").alias("w"))
        .group_by("account_id")
        .agg(pl.col("w").n_unique().alias("weeks_ordered"))
    )
    rel = weeks_active.join(weeks_ordered, on="account_id", how="left").with_columns(
        pl.when(pl.col("weeks_active") > 0)
        .then(pl.col("weeks_ordered").fill_null(0) / pl.col("weeks_active"))
        .alias("reliability")
    )
    return (
        acc.select("account_id")
        .join(stab, on="account_id", how="left")
        .join(rel.select("account_id", "reliability"), on="account_id", how="left")
    )


def account_quality(
    *,
    accounts: pl.DataFrame,
    orders: pl.DataFrame,
    skus: pl.DataFrame,
    capacity_plan: pl.DataFrame,
    last_week: date,
    profile: str = "balanced",
    cfg: AqsConfig | None = None,
) -> pl.DataFrame:
    cfg = cfg or AqsConfig()
    weights = cfg.profiles[profile]
    unit_cost = skus.filter(pl.col("status") == "production")["unit_cost_inr_per_kg"].mean()
    cap_month = monthly_capacity_kg(capacity_plan, last_week)

    is_pipeline = pl.col("status") == "pipeline"
    df = accounts.join(
        _history_components(accounts, orders, last_week), on="account_id", how="left"
    ).with_columns(
        pl.when(is_pipeline)
        .then(pl.col("requested_kg_per_month"))
        .otherwise(pl.col("committed_kg_per_month"))
        .alias("volume"),
        (
            pl.col("outlets_count").cast(pl.Float64).log1p()
            * pl.col("regions_served").str.split("|").list.len()
        ).alias("reach"),
        (pl.col("contract_price_inr_per_kg") - unit_cost).alias("margin"),
        (pl.col("shortfall_penalty_inr_per_kg") * pl.col("committed_kg_per_month")).alias(
            "penalty"
        ),
        pl.col("months_history").fill_null(0),
        is_pipeline.alias("prior"),
    )

    ref = ~pl.col("prior")
    # Account-type priors for pipeline accounts, falling back to the reference mean.
    for c in PRIOR_COMPONENTS:
        type_mean = pl.col(c).filter(ref).mean().over("account_type")
        global_mean = pl.col(c).filter(ref).mean()
        df = df.with_columns(
            pl.when(pl.col("prior"))
            .then(pl.coalesce(type_mean, global_mean))
            .otherwise(pl.col(c))
            .alias(c)
        )

    z_cols = []
    for c in (*POSITIVE, "penalty"):
        mean = pl.col(c).filter(ref).mean()
        std = pl.col(c).filter(ref).std(ddof=0)
        z = pl.when(std > 0).then((pl.col(c) - mean) / std).otherwise(0.0)
        z_cols.append(
            pl.when(pl.col(c).is_null()).then(0.0).otherwise(z).fill_null(0.0).alias(f"z_{c}")
        )
    df = df.with_columns(z_cols)

    share = pl.col("volume") / cap_month if cap_month and np.isfinite(cap_month) else pl.lit(None)
    df = df.with_columns(share.alias("capacity_share"))
    aqs = (
        pl.sum_horizontal([weights[c] * pl.col(f"z_{c}") for c in POSITIVE])
        - weights["penalty"] * pl.col("z_penalty")
        - weights["concentration"]
        * (pl.col("capacity_share").fill_null(0.0) / cfg.concentration_cap)
    )
    conf = (
        pl.when(pl.col("prior"))
        .then(0.0)
        .otherwise(pl.col("months_history") / (pl.col("months_history") + cfg.shrinkage_k_months))
    )
    df = df.with_columns(
        aqs.alias("aqs"),
        conf.alias("confidence"),
        (pl.col("capacity_share") > cfg.concentration_cap)
        .fill_null(False)
        .alias("exceeds_concentration_cap"),
        pl.when(pl.col("prior"))
        .then(pl.lit(list(PRIOR_COMPONENTS)))
        .otherwise(pl.lit([], dtype=pl.List(pl.String)))
        .alias("prior_components"),
        pl.lit(profile).alias("profile"),
    )
    return df.select(
        "account_id", "account_type", "status", "region_id", "aqs", "confidence", "prior",
        "prior_components", "profile", "capacity_share", "exceeds_concentration_cap",
        *POSITIVE, "penalty", *(f"z_{c}" for c in (*POSITIVE, "penalty")), "months_history",
    ).sort("aqs", descending=True)  # fmt: skip

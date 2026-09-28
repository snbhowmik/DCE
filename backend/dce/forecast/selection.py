"""Per-series model selection from backtest scores (ARCH §5.4, PRD FR-8)."""

from __future__ import annotations

import polars as pl

BASELINE = "seasonal_naive_52"


def select_models(scores: pl.DataFrame, baseline: str = BASELINE) -> pl.DataFrame:
    """Lowest mean pinball, provided it beats the baseline on MASE; else the baseline.

    Returns `series_id, model, reason, pinball, mase, baseline_mase`. When the baseline's MASE
    is undefined (constant training history) the lowest-pinball model is taken.
    """
    base = scores.filter(pl.col("model") == baseline).select(
        "series_id", pl.col("mase").alias("baseline_mase")
    )
    s = scores.join(base, on="series_id", how="left")
    beats = pl.col("baseline_mase").is_null() | (
        pl.col("mase").is_not_null() & (pl.col("mase") < pl.col("baseline_mase"))
    )
    eligible = s.filter((pl.col("model") == baseline) | beats)
    best = (
        eligible.sort("series_id", "pinball", "model", nulls_last=True)
        .group_by("series_id", maintain_order=True)
        .first()
    )
    reason = (
        pl.when(pl.col("model") != baseline)
        .then(pl.lit("lowest pinball; beats baseline MASE"))
        .when(pl.col("n_candidates") > 0)
        .then(pl.lit("baseline: lowest pinball among eligible"))
        .otherwise(pl.lit("baseline fallback: no model beat seasonal-naive MASE"))
    )
    n_cand = (
        s.filter((pl.col("model") != baseline) & beats)
        .group_by("series_id")
        .len(name="n_candidates")
    )
    return (
        best.join(n_cand, on="series_id", how="left")
        .with_columns(pl.col("n_candidates").fill_null(0))
        .with_columns(reason.alias("reason"))
        .select("series_id", "model", "reason", "pinball", "mase", "baseline_mase")
        .sort("series_id")
    )

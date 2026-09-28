"""Split-conformal calibration of P10/P90 (ARCH §5.4, D-004).

One-sided conformalized quantile regression: with nonconformity scores
  s_lo = q10 − y   (> 0 when y falls below the band)
  s_hi = y − q90   (> 0 when y falls above the band)
the adjustment for each side is the ⌈(n+1)(1−α)⌉/n empirical quantile of its scores
(α = 0.1 per side), so q10' = q10 − a_lo and q90' = q90 + a_hi. Adjustments may be negative
(narrowing an over-wide band). Per series when ≥ `min_points` backtest residuals exist, else
pooled over `pool_keys`.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

ALPHA_SIDE = 0.1


def _conformal_q(scores: np.ndarray, alpha: float = ALPHA_SIDE) -> float:
    n = len(scores)
    if n == 0:
        return 0.0
    level = min(1.0, math.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))


def conformal_adjustments(
    bt: pl.DataFrame,
    groups: pl.DataFrame,
    min_points: int = 30,
    pool_keys: tuple[str, ...] = ("channel", "tier"),
) -> pl.DataFrame:
    """`bt`: backtest rows of the chosen model per series (series_id, y, q10, q90).
    `groups`: series_id → pool keys. Returns `series_id, a_lo, a_hi, n, pooled`.
    """
    b = bt.join(groups, on="series_id", how="left").with_columns(
        (pl.col("q10") - pl.col("y")).alias("s_lo"), (pl.col("y") - pl.col("q90")).alias("s_hi")
    )
    pooled = {}
    for key, g in b.group_by(list(pool_keys)):
        pooled[key] = (_conformal_q(g["s_lo"].to_numpy()), _conformal_q(g["s_hi"].to_numpy()))
    rows = []
    for (sid,), g in b.group_by(["series_id"], maintain_order=True):
        n = g.height
        if n >= min_points:
            a_lo, a_hi = _conformal_q(g["s_lo"].to_numpy()), _conformal_q(g["s_hi"].to_numpy())
            is_pooled = False
        else:
            key = tuple(g.row(0, named=True)[k] for k in pool_keys)
            a_lo, a_hi = pooled.get(key, (0.0, 0.0))
            is_pooled = True
        rows.append((sid, a_lo, a_hi, n, is_pooled))
    return pl.DataFrame(
        rows,
        schema={
            "series_id": pl.String,
            "a_lo": pl.Float64,
            "a_hi": pl.Float64,
            "n": pl.Int64,
            "pooled": pl.Boolean,
        },
        orient="row",
    )


def apply_adjustments(pred: pl.DataFrame, adj: pl.DataFrame) -> pl.DataFrame:
    """q10' = max(0, min(q10 − a_lo, q50)); q90' = max(q90 + a_hi, q50)."""
    return (
        pred.join(adj.select("series_id", "a_lo", "a_hi"), on="series_id", how="left")
        .with_columns(pl.col("a_lo", "a_hi").fill_null(0.0))
        .with_columns(
            pl.min_horizontal(pl.col("q10") - pl.col("a_lo"), pl.col("q50"))
            .clip(lower_bound=0.0)
            .alias("q10"),
            pl.max_horizontal(pl.col("q90") + pl.col("a_hi"), pl.col("q50")).alias("q90"),
        )
        .drop("a_lo", "a_hi")
    )


def cross_fit_coverage(
    bt: pl.DataFrame, groups: pl.DataFrame, min_points: int = 30
) -> pl.DataFrame:
    """Honest calibrated coverage: calibrate on all folds but k, measure on fold k.

    Returns per series: `coverage_raw, coverage_calibrated, n`.
    """
    parts = []
    for k in bt["fold"].unique().sort().to_list():
        train = bt.filter(pl.col("fold") != k)
        test = bt.filter(pl.col("fold") == k)
        adj = conformal_adjustments(train, groups, min_points=max(1, min_points * 5 // 6))
        parts.append(apply_adjustments(test, adj).with_columns(pl.lit(k).alias("_k")))
    cal = pl.concat(parts)
    raw_cov = bt.group_by("series_id").agg(
        pl.col("y").is_between(pl.col("q10"), pl.col("q90")).mean().alias("coverage_raw"),
        pl.len().alias("n"),
    )
    cal_cov = cal.group_by("series_id").agg(
        pl.col("y").is_between(pl.col("q10"), pl.col("q90")).mean().alias("coverage_calibrated")
    )
    return raw_cov.join(cal_cov, on="series_id", how="left").sort("series_id")

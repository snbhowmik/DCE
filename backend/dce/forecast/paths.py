"""Sample paths by block bootstrap of backtest residuals (ARCH §5.4).

For each series, residual trajectories e[fold, h] = y − q50 of the chosen model come from the
backtest. A path concatenates blocks of `block` consecutive horizon steps, each block taken
from a randomly drawn fold at the same horizon steps, which keeps within-block autocorrelation
and horizon-dependent spread. Residuals are centered on their median, and each tail is rescaled
so the paths' P10/P90 match the calibrated bands. Paths are clipped at 0.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from dce.seeds import rng as seeded_rng


def residual_matrix(bt: pl.DataFrame, series_id: str, H: int) -> np.ndarray:
    """[n_folds, H] residual trajectories (NaN where a fold lacks a step)."""
    g = bt.filter(pl.col("series_id") == series_id)
    folds = sorted(g["fold"].unique().to_list())
    E = np.full((len(folds), H), np.nan)
    f_idx = {f: i for i, f in enumerate(folds)}
    for fold, h, y, q50 in g.select("fold", "h", "y", "q50").iter_rows():
        if 1 <= h <= H:
            E[f_idx[fold], h - 1] = y - q50
    return E


def block_bootstrap(
    E: np.ndarray, n_paths: int, block: int, gen: np.random.Generator
) -> np.ndarray:
    """[n_paths, H] residual paths from fold trajectories E [n_folds, H]."""
    n_folds, H = E.shape
    out = np.zeros((n_paths, H))
    if n_folds == 0:
        return out
    for start in range(0, H, block):
        stop = min(start + block, H)
        pick = gen.integers(0, n_folds, n_paths)
        out[:, start:stop] = E[pick, start:stop]
    # Fill any NaN from ragged folds with a draw from the pooled residuals.
    pool = E[np.isfinite(E)]
    nan = ~np.isfinite(out)
    if nan.any():
        out[nan] = gen.choice(pool, nan.sum()) if pool.size else 0.0
    return out


def rescale_tails(
    R: np.ndarray, pooled: np.ndarray, d10: np.ndarray, d90: np.ndarray
) -> np.ndarray:
    """Map residual paths so their 10th/90th percentiles ≈ d10/d90 (per horizon step).

    `pooled` is the series' residual sample used to estimate the raw tail quantiles.
    """
    if pooled.size == 0:
        return R
    med = float(np.median(pooled))
    r10, r90 = np.quantile(pooled - med, [0.1, 0.9])
    Rc = R - med
    lo = d10 / r10 if r10 < 0 else np.ones_like(d10)  # both ≤ 0 → non-negative scale
    hi = d90 / r90 if r90 > 0 else np.ones_like(d90)
    return np.where(Rc < 0, Rc * lo, Rc * hi)


def sample_paths(
    forecast: pl.DataFrame,
    bt: pl.DataFrame,
    *,
    n_paths: int,
    seed: int,
    block: int = 4,
) -> tuple[list[str], np.ndarray]:
    """`forecast`: calibrated `series_id, week_start, q10, q50, q90` (sorted by week).
    Returns (series order, paths [S, n_paths, H]).
    """
    sids = sorted(forecast["series_id"].unique().to_list())
    H = forecast.filter(pl.col("series_id") == sids[0]).height if sids else 0
    paths = np.zeros((len(sids), n_paths, H))
    for i, sid in enumerate(sids):
        f = forecast.filter(pl.col("series_id") == sid).sort("week_start")
        q10, q50, q90 = (f[c].to_numpy() for c in ("q10", "q50", "q90"))
        E = residual_matrix(bt, sid, H)
        gen = seeded_rng(seed, "forecast_paths", sid)
        R = block_bootstrap(E, n_paths, block, gen)
        R = rescale_tails(R, E[np.isfinite(E)], q10 - q50, q90 - q50)
        paths[i] = np.maximum(q50[None, :] + R, 0.0)
    return sids, paths


def paths_frame(sids: list[str], weeks: list, paths: np.ndarray) -> pl.DataFrame:
    """Long form `series_id, path, week_start, value` for Parquet."""
    S, P, H = paths.shape
    return pl.DataFrame(
        {
            "series_id": np.repeat(sids, P * H),
            "path": np.tile(np.repeat(np.arange(P), H), S),
            "week_start": weeks * (S * P),
            "value": paths.ravel(),
        }
    )

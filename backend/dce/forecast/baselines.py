"""Baseline forecasters (ARCH §5.4, T2.2); always computed, and the fallback for selection.

P50 is the rule's point forecast (so MASE compares against the textbook baseline). P10/P90 add
the 10th/90th percentiles of the rule's empirical residuals on the training history, per horizon
step where the rule's error depends on h. All quantiles are clipped at 0 (demand ≥ 0).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import polars as pl

from dce.forecast.backtest import PREDICTION_SCHEMA, QUANTILES, empty_prediction


def _quantile_frame(
    series_id: str, horizon: Sequence[date], point: np.ndarray, resid: list[np.ndarray]
) -> pl.DataFrame:
    """`resid[h]`: residual sample for step h (falls back to pooled if too small)."""
    pooled = np.concatenate([r for r in resid if r.size]) if any(r.size for r in resid) else None
    rows = []
    for h, wk in enumerate(horizon):
        r = resid[h] if resid[h].size >= 8 else pooled
        offs = np.quantile(r, QUANTILES) if r is not None and r.size else np.zeros(len(QUANTILES))
        offs[QUANTILES.index(0.5)] = 0.0
        lo, mid, hi = np.maximum(point[h] + offs, 0.0)
        # No crossing, with P50 anchored at the rule's value.
        rows.append((series_id, wk, min(lo, mid), mid, max(hi, mid)))
    return pl.DataFrame(
        rows,
        schema=PREDICTION_SCHEMA,
        orient="row",
    )


def _per_series(history: pl.DataFrame) -> list[tuple[str, np.ndarray]]:
    out = []
    for (sid,), g in history.sort("week_start").group_by(["series_id"], maintain_order=True):
        out.append((str(sid), g["y"].to_numpy().astype(float)))
    return out


class SeasonalNaive:
    """ŷ(t) = y(t − m); last value if the history is shorter than m + 1 weeks."""

    def __init__(self, m: int = 52) -> None:
        self.m = m
        self.name = f"seasonal_naive_{m}"

    def fit_predict(
        self, history: pl.DataFrame, horizon: Sequence[date], future: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        frames = []
        H = len(horizon)
        for sid, y in _per_series(history):
            n = len(y)
            if n > self.m and self.m >= H:
                point = np.array([y[n - self.m + h] for h in range(H)])
                r = y[self.m :] - y[: -self.m]
                resid = [r] * H
            else:  # naive-1 fallback: h-step residuals of the last-value rule
                point = np.full(H, y[-1] if n else 0.0)
                resid = [
                    y[h + 1 :] - y[: n - h - 1] if n > h + 1 else np.array([]) for h in range(H)
                ]
            frames.append(_quantile_frame(sid, horizon, point, resid))
        return pl.concat(frames) if frames else empty_prediction()


class WindowAverage:
    """ŷ(t+h) = mean of the last w weeks, flat over the horizon."""

    def __init__(self, w: int = 8) -> None:
        self.w = w
        self.name = f"window_average_{w}"

    def fit_predict(
        self, history: pl.DataFrame, horizon: Sequence[date], future: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        frames = []
        H = len(horizon)
        for sid, y in _per_series(history):
            n = len(y)
            w = min(self.w, n)
            point = np.full(H, y[-w:].mean() if n else 0.0)
            # h-step residuals: y[t+h] − mean(y[t−w+1 .. t]) for every past origin t
            resid = []
            if n > self.w:
                csum = np.concatenate([[0.0], np.cumsum(y)])
                means = (csum[self.w :] - csum[: -self.w]) / self.w  # mean ending at index i+w−1
                for h in range(H):
                    k = n - self.w - h  # number of origins with a target inside history
                    resid.append(
                        y[self.w + h : self.w + h + k] - means[:k] if k > 0 else np.array([])
                    )
            else:
                resid = [np.array([]) for _ in range(H)]
            frames.append(_quantile_frame(sid, horizon, point, resid))
        return pl.concat(frames) if frames else empty_prediction()

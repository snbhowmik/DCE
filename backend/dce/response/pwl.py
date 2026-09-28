"""Piecewise-linear concave response for the optimizer (ARCH §5.6, T4.2).

The steady-state weekly response f(s) = β·Hill(s/(1−θ)) on [0, cap] is replaced by its upper
concave envelope (least concave majorant; identical to f when α ≤ 1) and then by K chords with
breakpoints at equal arc length of the normalized curve. Chords of a concave function have
non-increasing slopes, so the maximizing LP needs no binaries. `cap` = max observed weekly
spend × extrapolation factor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dce.response.fit import ResponseFit

GRID = 400


def concave_majorant(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Least concave majorant of points (x ascending) evaluated at x (upper hull)."""
    hull: list[int] = []
    for i in range(len(x)):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            # drop b if it lies on/below the chord a→i (keeps the hull concave)
            if (y[b] - y[a]) * (x[i] - x[a]) <= (y[i] - y[a]) * (x[b] - x[a]):
                hull.pop()
            else:
                break
        hull.append(i)
    return np.interp(x, x[hull], y[hull])


@dataclass(frozen=True)
class ResponseCurve:
    region_id: str
    breakpoints: np.ndarray  # [K+1] weekly spend, from 0 to cap
    widths: np.ndarray  # [K] ₹/week
    slopes: np.ndarray  # [K] kg lift per week per ₹/week, non-increasing
    cap: float
    low_confidence: bool
    max_abs_error: float  # max |PWL − f| on the grid (envelope gap included)

    def evaluate(self, weekly_spend: np.ndarray | float) -> np.ndarray:
        s = np.clip(np.asarray(weekly_spend, dtype=float), 0.0, self.cap)
        lift = np.concatenate([[0.0], np.cumsum(self.widths * self.slopes)])
        return np.interp(s, self.breakpoints, lift)


def _breakpoints(x: np.ndarray, env: np.ndarray, k: int) -> np.ndarray:
    """Spend levels at equal arc length of the normalized envelope (x/cap, env/max).

    Dense where the curve bends steeply, still spread across flat tails, so the chord error is
    balanced along the curve (equal-spend spacing misses a steep start, equal-lift spacing a
    flat top).
    """
    top = env[-1]
    if top <= 0:
        return np.linspace(x[0], x[-1], k + 1)
    xn, yn = x / x[-1], env / top
    arc = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xn), np.diff(yn)))])
    bp = np.interp(np.linspace(0.0, arc[-1], k + 1), arc, x)
    return np.maximum.accumulate(bp)


def linearize(fit: ResponseFit, k: int = 6) -> ResponseCurve:
    cap = max(fit.spend_cap, 0.0)
    if cap <= 0 or fit.beta <= 0:
        bp = np.linspace(0.0, max(cap, 1.0), k + 1)
        zero = np.zeros(k)
        return ResponseCurve(fit.region_id, bp, np.diff(bp), zero, cap, fit.low_confidence, 0.0)
    x = np.linspace(0.0, cap, GRID)
    f = fit.steady_lift(x)
    env = concave_majorant(x, f)
    bp = _breakpoints(x, env, k)
    at_bp = np.interp(bp, x, env)
    widths = np.diff(bp)
    slopes = np.diff(at_bp) / widths
    slopes = np.minimum.accumulate(np.maximum(slopes, 0.0))  # guard float noise
    curve = ResponseCurve(fit.region_id, bp, widths, slopes, cap, fit.low_confidence, 0.0)
    err = float(np.max(np.abs(curve.evaluate(x) - f)))
    return ResponseCurve(fit.region_id, bp, widths, slopes, cap, fit.low_confidence, err)

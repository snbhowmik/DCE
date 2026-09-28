"""T4.2: K segments, non-increasing slopes (concavity), extrapolation cap."""

from __future__ import annotations

import numpy as np
import pytest

from dce.response.fit import ResponseFit
from dce.response.pwl import concave_majorant, linearize


def make_fit(
    alpha: float, beta: float = 40.0, max_spend: float = 8000.0, low: bool = False
) -> ResponseFit:
    return ResponseFit(
        region_id="R", n_weeks=110, theta=0.5, alpha=alpha, kappa=6000.0, beta=beta,
        organic_coef=np.zeros(6), r2=0.9, mean_spend=3000.0, max_spend=max_spend,
        spend_cap=max_spend * 1.5, elasticity=0.2, lift_at_mean=10.0, ci={},
        low_confidence=low,
    )  # fmt: skip


@pytest.mark.parametrize("alpha", [0.6, 1.0, 1.6, 2.8])
def test_segments_concave_and_capped(alpha: float) -> None:
    fit = make_fit(alpha)
    curve = linearize(fit, k=6)
    assert len(curve.widths) == len(curve.slopes) == 6
    assert (np.diff(curve.slopes) <= 1e-12).all()  # non-increasing marginal lift
    assert (curve.slopes >= 0).all()
    assert curve.cap == pytest.approx(8000 * 1.5)
    assert curve.breakpoints[0] == 0 and curve.breakpoints[-1] == pytest.approx(curve.cap)
    assert curve.widths.sum() == pytest.approx(curve.cap)
    # beyond the cap, no extra lift (extrapolation guard)
    assert curve.evaluate(curve.cap * 3) == pytest.approx(curve.evaluate(curve.cap))


def test_pwl_is_concave_function() -> None:
    curve = linearize(make_fit(1.6), k=8)
    x = np.linspace(0, curve.cap, 300)
    y = curve.evaluate(x)
    for a, b, lam in [(0.0, curve.cap, 0.3), (1000.0, 9000.0, 0.5), (2000.0, 4000.0, 0.7)]:
        mid = lam * a + (1 - lam) * b
        assert curve.evaluate(mid) >= lam * curve.evaluate(a) + (1 - lam) * curve.evaluate(b) - 1e-9
    assert (np.diff(y) >= -1e-12).all()  # monotone non-decreasing


def test_concave_curve_approximation_is_close() -> None:
    fit = make_fit(0.8)
    curve = linearize(fit, k=6)
    x = np.linspace(0, curve.cap, 200)
    f = fit.steady_lift(x)
    assert np.max(np.abs(curve.evaluate(x) - f)) == pytest.approx(curve.max_abs_error, rel=0.1)
    assert curve.max_abs_error < 0.1 * f.max()
    assert (curve.evaluate(x) <= f + 1e-9).all()  # chords of a concave curve lie below it


def test_s_curve_uses_envelope() -> None:
    fit = make_fit(2.8)
    curve = linearize(fit, k=6)
    x = np.linspace(0, curve.cap, 200)
    assert (curve.evaluate(x) >= fit.steady_lift(x) - 0.05 * fit.steady_lift(x).max()).all()


def test_concave_majorant_basics() -> None:
    x = np.array([0.0, 1.0, 2.0, 3.0])
    y = np.array([0.0, 0.0, 3.0, 3.5])  # convex start
    env = concave_majorant(x, y)
    assert env.tolist() == pytest.approx([0.0, 1.5, 3.0, 3.5])
    assert (env >= y).all()


def test_zero_response_and_flags() -> None:
    curve = linearize(make_fit(1.0, beta=0.0, low=True))
    assert (curve.slopes == 0).all() and curve.low_confidence

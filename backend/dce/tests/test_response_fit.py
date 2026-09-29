"""T4.1: adstock + Hill fitter recovers known parameters; bootstrap CIs; low_confidence logic.

The parameters below are test-only and exist solely to unit-test the fitter (TASK T4.1).
"""

from __future__ import annotations

import numpy as np
import pytest

from dce.response.fit import ResponseConfig, adstock, fit_region, hill

TRUE = {"theta": 0.5, "alpha": 1.6, "kappa": 6000.0, "beta": 40.0}
N = 156
CFG = ResponseConfig(n_boot=30)


def simulate(effect: bool = True, noise: float = 2.0, spend_var: bool = True, seed: int = 0):
    rng = np.random.default_rng(seed)
    t = np.arange(N)
    if spend_var:
        levels = rng.choice([500.0, 1500.0, 3000.0, 5000.0, 8000.0], size=N // 6 + 1)
        spend = np.repeat(levels, 6)[:N] * rng.uniform(0.9, 1.1, N)
    else:
        spend = np.full(N, 3000.0)
    organic = 60 + 0.05 * t + 12 * np.sin(2 * np.pi * t / 52.18)
    a = adstock(spend, TRUE["theta"])
    lift = TRUE["beta"] * hill(a, TRUE["alpha"], TRUE["kappa"]) if effect else 0.0
    y = organic + lift + rng.normal(0, noise, N)
    return y, spend


@pytest.fixture(scope="module")
def fit():
    y, s = simulate()
    return fit_region("R", y, s, CFG, seed=1)


def test_recovers_parameters(fit) -> None:
    assert fit.theta == pytest.approx(TRUE["theta"], abs=0.08)
    assert fit.alpha == pytest.approx(TRUE["alpha"], rel=0.3)
    assert fit.kappa == pytest.approx(TRUE["kappa"], rel=0.3)
    assert fit.beta == pytest.approx(TRUE["beta"], rel=0.25)
    # the response curve itself is what the optimizer uses: check it closely
    grid = np.array([500.0, 2000.0, 4000.0, 8000.0])
    true_lift = TRUE["beta"] * hill(grid / (1 - TRUE["theta"]), TRUE["alpha"], TRUE["kappa"])
    assert fit.steady_lift(grid) == pytest.approx(true_lift, rel=0.12, abs=1.0)
    assert fit.r2 > 0.9
    assert not fit.low_confidence, fit.reasons


def test_bootstrap_cis_cover_truth() -> None:
    """A single 90% interval may miss; coverage across replications is the real property."""
    hits_theta = hits_lift = 0
    reps = 8
    for k in range(reps):
        y, s = simulate(seed=200 + k)
        f = fit_region("R", y, s, ResponseConfig(n_boot=20), seed=k)
        hits_theta += f.ci["theta"][0] <= TRUE["theta"] <= f.ci["theta"][1]
        a = f.mean_spend / (1 - TRUE["theta"])
        true_lift = TRUE["beta"] * hill(np.array([a]), TRUE["alpha"], TRUE["kappa"])[0]
        hits_lift += f.ci["lift_at_mean"][0] <= true_lift <= f.ci["lift_at_mean"][1]
        assert set(f.ci) == {"theta", "alpha", "kappa", "beta", "lift_at_mean"}
    assert hits_theta / reps >= 0.6
    assert hits_lift / reps >= 0.6


def test_extrapolation_cap_and_lags(fit) -> None:
    assert fit.spend_cap == pytest.approx(fit.max_spend * 1.5)
    w = fit.lag_weights(8)
    assert w.sum() == pytest.approx(1.0) and (np.diff(w) <= 0).all()


def test_constant_spend_is_low_confidence() -> None:
    y, s = simulate(spend_var=False)
    f = fit_region("R", y, s, CFG, seed=2)
    assert f.low_confidence and any("barely varies" in r for r in f.reasons)


def test_no_effect_is_low_confidence() -> None:
    y, s = simulate(effect=False, noise=6.0, seed=3)
    f = fit_region("R", y, s, CFG, seed=3)
    assert f.low_confidence
    assert any("bootstrap" in r or "elasticity" in r for r in f.reasons)


def test_implausible_elasticity_flag() -> None:
    y, s = simulate()
    f = fit_region("R", y, s, ResponseConfig(n_boot=5, elasticity_bounds=(0.0, 0.01)), seed=4)
    assert f.low_confidence and any("elasticity" in r for r in f.reasons)


def test_deterministic() -> None:
    y, s = simulate(seed=5)
    a = fit_region("R", y, s, ResponseConfig(n_boot=5), seed=9)
    b = fit_region("R", y, s, ResponseConfig(n_boot=5), seed=9)
    assert a.summary() == b.summary()


def test_dataset_driver_and_mode_invariance(tiny_tables, tiny_window) -> None:
    """Code paths on the fixture (no accuracy claims) + RESP never depends on the mode."""
    from dce.runner import build_run_config, response_stage
    from dce.strategy import load_strategy_modes

    small = {"app": {"response": {"n_boot": 5, "n_starts": 2}}}
    hashes = set()
    for mode in load_strategy_modes():
        rs = response_stage(tiny_tables, tiny_window, build_run_config(mode, seed=3, **small))
        hashes.add(rs.artifact_hash())
    assert len(hashes) == 1
    assert set(rs.fits) | set(rs.skipped) == {"R_N", "R_S"}
    assert rs.summary().height == len(rs.fits)


def test_sparse_spend_history_is_low_confidence() -> None:
    """Spend present only in the first weeks (then zero) cannot identify a response."""
    y, s = simulate(seed=11)
    s = s.copy()
    s[20:] = 0.0
    f = fit_region("R", y, s, ResponseConfig(n_boot=5), seed=1)
    assert f.low_confidence and any("weeks with spend" in r for r in f.reasons)


def test_bound_guard() -> None:
    from dce.response.fit import _bound_reasons, _Solve

    _, s = simulate(seed=12)
    cfg = ResponseConfig()
    pinned = _Solve(0.9, 0.5, 1e9, 1.0, np.zeros(6), 0.0, np.zeros(N))
    reasons = _bound_reasons(pinned, s, cfg)
    assert {r.split()[0] for r in reasons} == {"theta", "alpha", "kappa"}
    interior = _Solve(0.5, 1.5, float(adstock(s, 0.45).mean()), 1.0, np.zeros(6), 0.0, np.zeros(N))
    assert _bound_reasons(interior, s, cfg) == []

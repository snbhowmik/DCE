"""T2.6: selection with baseline fallback, conformal calibration, block-bootstrapped paths."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from dce.forecast.calibration import (
    apply_adjustments,
    conformal_adjustments,
    cross_fit_coverage,
)
from dce.forecast.paths import block_bootstrap, sample_paths
from dce.forecast.pipeline import ForecastConfig, ForecastSet, run_forecast
from dce.forecast.selection import select_models

W0 = date(2024, 1, 1)


def test_selection_rules() -> None:
    rows = [
        # A: lightgbm best pinball and beats baseline MASE
        ("A", "seasonal_naive_52", 1.0, 5.0), ("A", "lightgbm", 0.8, 3.0), ("A", "auto_ets", 0.9, 4.0),
        # B: ets best pinball but worse MASE than baseline → next eligible (theta)
        ("B", "seasonal_naive_52", 1.0, 5.0), ("B", "auto_ets", 0.5, 6.0), ("B", "auto_theta", 0.7, 4.5),
        # C: nothing beats baseline MASE → fallback
        ("C", "seasonal_naive_52", 0.5, 5.0), ("C", "lightgbm", 1.2, 1.2), ("C", "auto_ets", 1.1, 1.1),
        # D: baseline MASE undefined → lowest pinball
        ("D", "seasonal_naive_52", None, 5.0), ("D", "window_average_8", None, 2.0),
    ]  # fmt: skip
    scores = pl.DataFrame(rows, schema=["series_id", "model", "mase", "pinball"], orient="row")
    sel = {r["series_id"]: r for r in select_models(scores).iter_rows(named=True)}
    assert sel["A"]["model"] == "lightgbm"
    assert sel["B"]["model"] == "auto_theta"
    assert sel["C"]["model"] == "seasonal_naive_52" and "fallback" in sel["C"]["reason"]
    assert sel["D"]["model"] == "window_average_8"


def _bt(n_series: int, n_folds: int, H: int, sd: float, seed: int = 0) -> pl.DataFrame:
    """Actuals ~ N(100, sd) against a zero-width raw band at 100."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_series):
        for f in range(n_folds):
            for h in range(1, H + 1):
                y = float(100 + rng.normal(0, sd))
                rows.append((f"s{s}", f, h, y, 100.0, 100.0, 100.0))
    return pl.DataFrame(
        rows, schema=["series_id", "fold", "h", "y", "q10", "q50", "q90"], orient="row"
    )


def test_conformal_widens_to_nominal() -> None:
    bt = _bt(3, 6, 13, 1.0)
    groups = pl.DataFrame(
        {"series_id": ["s0", "s1", "s2"], "channel": ["D2C"] * 3, "tier": ["m"] * 3}
    )
    adj = conformal_adjustments(bt, groups, min_points=30)
    assert not adj["pooled"].any()
    assert adj["a_lo"].to_numpy() == pytest.approx([1.28] * 3, abs=0.4)
    cov = cross_fit_coverage(bt, groups)
    assert (cov["coverage_raw"] < 0.05).all()
    assert cov["coverage_calibrated"].mean() == pytest.approx(0.8, abs=0.1)


def test_conformal_pools_small_series() -> None:
    bt = pl.concat(
        [
            _bt(1, 6, 13, 1.0),
            _bt(1, 1, 5, 1.0, seed=2).with_columns(pl.lit("tiny").alias("series_id")),
        ]
    )
    groups = pl.DataFrame({"series_id": ["s0", "tiny"], "channel": ["D2C"] * 2, "tier": ["m"] * 2})
    adj = {r["series_id"]: r for r in conformal_adjustments(bt, groups).iter_rows(named=True)}
    assert adj["tiny"]["pooled"] and not adj["s0"]["pooled"]


def test_apply_adjustments_keeps_order_and_nonneg() -> None:
    pred = pl.DataFrame({"series_id": ["a"], "q10": [5.0], "q50": [6.0], "q90": [7.0]})
    adj = pl.DataFrame({"series_id": ["a"], "a_lo": [10.0], "a_hi": [-5.0]})
    out = apply_adjustments(pred, adj).row(0, named=True)
    assert out["q10"] == 0.0 and out["q90"] == 6.0 and out["q50"] == 6.0


def test_block_bootstrap_preserves_within_block_structure() -> None:
    # each fold trajectory is a constant offset: within a block, a path must stay constant
    E = np.repeat(np.array([[-2.0], [0.0], [3.0]]), 12, axis=1)
    R = block_bootstrap(E, 200, 4, np.random.default_rng(0))
    for start in (0, 4, 8):
        block = R[:, start : start + 4]
        assert (block == block[:, :1]).all()
    assert set(np.unique(R)) <= {-2.0, 0.0, 3.0}


def test_paths_match_calibrated_bands() -> None:
    H = 13
    weeks = [W0 + timedelta(weeks=h) for h in range(H)]
    fc = pl.DataFrame(
        {
            "series_id": ["s0"] * H,
            "week_start": weeks,
            "q10": [80.0] * H,
            "q50": [100.0] * H,
            "q90": [130.0] * H,
        }
    )
    bt = _bt(1, 6, H, 7.0)
    sids, P = sample_paths(fc, bt, n_paths=2000, seed=1)
    assert sids == ["s0"] and P.shape == (1, 2000, H)
    q10, q50, q90 = np.quantile(P[0], [0.1, 0.5, 0.9], axis=0)
    assert q10.mean() == pytest.approx(80, abs=6)
    assert q50.mean() == pytest.approx(100, abs=6)
    assert q90.mean() == pytest.approx(130, abs=8)
    assert (P >= 0).all()
    _, P2 = sample_paths(fc, bt, n_paths=2000, seed=1)
    assert np.array_equal(P, P2)


def _series(n: int = 110) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(5)
    t = np.arange(n)
    ys = {
        "D2C|seasonal|S": 50 + 20 * np.sin(2 * np.pi * t / 52),  # exact: SN is perfect → fallback
        "D2C|noisy|S": 40 + rng.normal(0, 4, n),
        "D2C|trend|S": 10 + 0.4 * t + rng.normal(0, 1, n),
    }
    wk = [W0 + timedelta(weeks=i) for i in range(n)]
    series = pl.concat(
        pl.DataFrame({"series_id": [k] * n, "week_start": wk, "y": list(v), "channel": ["D2C"] * n})
        for k, v in ys.items()
    )
    groups = pl.DataFrame({"series_id": list(ys), "channel": ["D2C"] * 3, "tier": ["metro"] * 3})
    return series, groups


@pytest.fixture(scope="module")
def fset() -> ForecastSet:
    series, groups = _series()
    cfg = ForecastConfig(n_paths=200, models=("seasonal_naive_52", "window_average_8", "auto_ets"))
    return run_forecast(series, groups, None, cfg, seed=11)


def test_pipeline_outputs(fset: ForecastSet) -> None:
    q = fset.quantiles
    assert q.height == 3 * 13 and q["h"].min() == 1 and q["h"].max() == 13
    assert (q["q10"] <= q["q50"]).all() and (q["q50"] <= q["q90"]).all()
    assert fset.paths.shape == (3, 200, 13)
    assert set(fset.selection["series_id"]) == set(q["series_id"])
    assert fset.selection["reason"].is_not_null().all()
    assert {"coverage_raw", "coverage_calibrated"} <= set(fset.coverage.columns)


def test_pipeline_baseline_fallback(fset: ForecastSet) -> None:
    sel = {r["series_id"]: r for r in fset.selection.iter_rows(named=True)}
    assert sel["D2C|seasonal|S"]["model"] == "seasonal_naive_52"
    assert sel["D2C|trend|S"]["model"] != "window_average_8"  # a flat mean loses on a trend


def test_pipeline_deterministic() -> None:
    series, groups = _series()
    cfg = ForecastConfig(n_paths=50, models=("seasonal_naive_52", "window_average_8", "lightgbm"))
    a = run_forecast(series, groups, None, cfg, seed=3)
    b = run_forecast(series, groups, None, cfg, seed=3)
    assert a.artifact_hash() == b.artifact_hash()
    c = run_forecast(series, groups, None, cfg, seed=4)
    assert c.artifact_hash() != a.artifact_hash()  # seed flows into paths


def test_pipeline_writes_artifacts(fset: ForecastSet, tmp_path: Path) -> None:
    files = fset.write(tmp_path)
    assert all(p.is_file() for p in files.values())
    paths = pl.read_parquet(files["forecast_paths"])
    assert paths.height == 3 * 200 * 13


def test_pipeline_on_fixture_all_models(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code-path smoke with every model and real covariates; no accuracy asserted."""
    from dce.demand.reconstruct import weekly_demand
    from dce.forecast.covariates import build_covariates, series_meta

    first, last = tiny_window
    demand = weekly_demand(tiny_tables["orders"], last)
    series = demand.select(
        "series_id", "week_start", pl.col("demand_kg").alias("y"), "channel", "is_censored"
    )
    cov = build_covariates(tiny_tables, series_meta(demand), first, last + timedelta(weeks=13))
    groups = cov.group_by("series_id").agg(pl.col("channel", "tier").first())
    fs = run_forecast(series, groups, cov, ForecastConfig(n_paths=100), seed=1)
    assert fs.paths.shape[0] == series["series_id"].n_unique()
    assert set(fs.scores["model"]) == {
        "seasonal_naive_52", "window_average_8", "auto_ets", "auto_theta", "lightgbm"
    }  # fmt: skip

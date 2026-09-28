"""Forecast pipeline: clean → backtest → select → fit → calibrate → sample paths (ARCH §5.4).

Strategy-mode agnostic by construction (IDEATION P1): nothing here takes or reads a mode.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from dce.forecast.anomaly import AnomalyConfig, detect_anomalies
from dce.forecast.backtest import (
    Forecaster,
    rolling_origin_folds,
    run_backtest,
    score_backtest,
)
from dce.forecast.baselines import SeasonalNaive, WindowAverage
from dce.forecast.calibration import (
    apply_adjustments,
    conformal_adjustments,
    cross_fit_coverage,
)
from dce.forecast.lgbm import LgbmConfig, LightGBMQuantile
from dce.forecast.paths import paths_frame, sample_paths
from dce.forecast.selection import BASELINE, select_models
from dce.forecast.statistical import auto_ets, auto_theta
from dce.logs import get_logger

log = get_logger(__name__)

ALL_MODELS = ("seasonal_naive_52", "window_average_8", "auto_ets", "auto_theta", "lightgbm")


@dataclass(frozen=True)
class ForecastConfig:
    horizon: int = 13
    n_folds: int = 6
    step_weeks: int = 4
    min_train_weeks: int = 52
    mase_seasonality: int = 52
    n_paths: int = 500
    block_weeks: int = 4
    calib_min_points: int = 30
    models: tuple[str, ...] = ALL_MODELS
    anomaly: AnomalyConfig = field(default_factory=AnomalyConfig)

    @classmethod
    def from_config(cls, app: dict[str, Any], scoring: dict[str, Any]) -> ForecastConfig:
        f = app.get("forecast", {})
        bt = f.get("backtest", {})
        d = cls()
        return cls(
            horizon=int(app.get("horizon_weeks", d.horizon)),
            n_folds=int(bt.get("n_folds", d.n_folds)),
            step_weeks=int(bt.get("step_weeks", d.step_weeks)),
            min_train_weeks=int(bt.get("min_train_weeks", d.min_train_weeks)),
            mase_seasonality=int(f.get("mase_seasonality", d.mase_seasonality)),
            n_paths=int(app.get("n_paths", d.n_paths)),
            block_weeks=int(f.get("block_weeks", d.block_weeks)),
            calib_min_points=int(f.get("calib_min_points", d.calib_min_points)),
            models=tuple(f.get("models", d.models)),
            anomaly=AnomalyConfig.from_scoring(scoring),
        )


def build_models(names: tuple[str, ...], seed: int) -> list[Forecaster]:
    registry: dict[str, Any] = {
        "seasonal_naive_52": lambda: SeasonalNaive(52),
        "window_average_8": lambda: WindowAverage(8),
        "auto_ets": auto_ets,
        "auto_theta": auto_theta,
        "lightgbm": lambda: LightGBMQuantile(LgbmConfig(seed=seed)),
    }
    if BASELINE not in names:
        names = (BASELINE, *names)  # the fallback must always be computed
    return [registry[n]() for n in names]


@dataclass
class ForecastSet:
    horizon: list[date]
    quantiles: pl.DataFrame  # series_id, week_start, h, model, q10, q50, q90, q10_raw, q90_raw
    path_series: list[str]
    paths: np.ndarray  # [S, n_paths, H]
    selection: pl.DataFrame
    scores: pl.DataFrame
    coverage: pl.DataFrame
    calibration: pl.DataFrame
    anomalies: pl.DataFrame

    def artifact_hash(self) -> str:
        """Content hash of the forecast (quantiles + paths), independent of file formats."""
        h = hashlib.sha256()
        buf = io.BytesIO()
        self.quantiles.sort("series_id", "week_start").write_csv(buf, float_precision=10)
        h.update(buf.getvalue())
        h.update("|".join(self.path_series).encode())
        h.update(np.ascontiguousarray(np.round(self.paths, 10)).tobytes())
        return h.hexdigest()

    def write(self, out: Path) -> dict[str, Path]:
        out.mkdir(parents=True, exist_ok=True)
        files = {
            "forecast_quantiles": out / "forecast_quantiles.parquet",
            "forecast_paths": out / "forecast_paths.parquet",
            "forecast_selection": out / "forecast_selection.parquet",
            "forecast_scores": out / "forecast_scores.parquet",
            "forecast_coverage": out / "forecast_coverage.parquet",
            "forecast_calibration": out / "forecast_calibration.parquet",
            "forecast_anomalies": out / "forecast_anomalies.parquet",
        }
        self.quantiles.write_parquet(files["forecast_quantiles"])
        paths_frame(self.path_series, self.horizon, self.paths).write_parquet(
            files["forecast_paths"]
        )
        self.selection.write_parquet(files["forecast_selection"])
        self.scores.write_parquet(files["forecast_scores"])
        self.coverage.write_parquet(files["forecast_coverage"])
        self.calibration.write_parquet(files["forecast_calibration"])
        self.anomalies.write_parquet(files["forecast_anomalies"])
        return files


def run_forecast(
    series: pl.DataFrame,
    groups: pl.DataFrame,
    covariates: pl.DataFrame | None,
    cfg: ForecastConfig,
    seed: int,
) -> ForecastSet:
    """`series`: `series_id, week_start, y, channel[, is_censored]`, zero-filled, one grid end.
    `groups`: `series_id, channel, tier` (conformal pooling keys).
    """
    last = series["week_start"].max()
    first = series["week_start"].min()
    assert isinstance(last, date) and isinstance(first, date)
    horizon = [last + timedelta(weeks=h) for h in range(1, cfg.horizon + 1)]

    flagged = detect_anomalies(series, cfg.anomaly)
    clean = flagged.select("series_id", "week_start", pl.col("y_clean").alias("y"))
    anomalies = flagged.filter(pl.col("is_anomaly")).select(
        "series_id", "week_start", "y", "baseline", "robust_z", "y_clean"
    )

    models = build_models(cfg.models, seed)
    folds = rolling_origin_folds(
        first,
        last,
        n_folds=cfg.n_folds,
        horizon=cfg.horizon,
        step=cfg.step_weeks,
        min_train_weeks=cfg.min_train_weeks,
    )
    if not folds:
        raise ValueError("history too short for any backtest fold")
    bt = run_backtest(clean, models, folds, covariates)
    scores = score_backtest(bt, clean, cfg.mase_seasonality)
    selection = select_models(scores)
    log.info(
        "forecast_selection",
        chosen=dict(selection.group_by("model").len().iter_rows()),
        n_series=selection.height,
    )
    bt_chosen = bt.join(selection.select("series_id", "model"), on=["series_id", "model"])
    calibration = conformal_adjustments(bt_chosen, groups, cfg.calib_min_points)
    coverage = cross_fit_coverage(bt_chosen, groups, cfg.calib_min_points)

    preds = []
    by_name = {m.name: m for m in models}
    for name, chosen in selection.group_by("model"):
        sids = chosen["series_id"].to_list()
        model = by_name[str(name[0])]
        # Global models learn from every series; local ones only need their own.
        hist = clean if name[0] == "lightgbm" else clean.filter(pl.col("series_id").is_in(sids))
        p = model.fit_predict(hist, horizon, covariates).filter(pl.col("series_id").is_in(sids))
        preds.append(p.with_columns(pl.lit(str(name[0])).alias("model")))
    raw = pl.concat(preds).with_columns(
        pl.col("q10").alias("q10_raw"), pl.col("q90").alias("q90_raw")
    )
    quantiles = (
        apply_adjustments(raw, calibration)
        .with_columns(
            ((pl.col("week_start") - pl.lit(horizon[0])).dt.total_days() // 7 + 1)
            .cast(pl.Int32)
            .alias("h")
        )
        .select("series_id", "week_start", "h", "model", "q10", "q50", "q90", "q10_raw", "q90_raw")
        .sort("series_id", "week_start")
    )
    path_series, paths = sample_paths(
        quantiles, bt_chosen, n_paths=cfg.n_paths, seed=seed, block=cfg.block_weeks
    )
    return ForecastSet(
        horizon=horizon,
        quantiles=quantiles,
        path_series=path_series,
        paths=paths,
        selection=selection,
        scores=scores,
        coverage=coverage,
        calibration=calibration,
        anomalies=anomalies,
    )

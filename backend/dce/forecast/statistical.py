"""statsforecast AutoETS / AutoTheta behind the `Forecaster` protocol (ARCH §5.4, T2.3).

P50 = model mean; P10/P90 = the model's 80% prediction interval. Series the model cannot fit
fall back to Naive (statsforecast `fallback_model`). Note: statsforecast's ETS supports season
lengths ≤ 24, so with weekly m = 52 AutoETS fits a non-seasonal model; weekly seasonality is
carried by SeasonalNaive and the LightGBM calendar features.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, cast

import polars as pl
from statsforecast import StatsForecast
from statsforecast.models import AutoETS, AutoTheta, Naive

from dce.forecast.backtest import empty_prediction

FREQ = "W-MON"


class StatsModel:
    def __init__(self, name: str, factory: Callable[[], Any]) -> None:
        self.name = name
        self._factory = factory

    def fit_predict(
        self, history: pl.DataFrame, horizon: Sequence[date], future: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        if history.is_empty():
            return empty_prediction()
        df = (
            history.select(
                pl.col("series_id").alias("unique_id"),
                pl.col("week_start").cast(pl.Datetime("ns")).alias("ds"),
                pl.col("y").cast(pl.Float64),
            )
            .sort("unique_id", "ds")
            .to_pandas()
        )
        # Series that stop before the origin are forecast through the gap, then cut to horizon.
        ends = history.group_by("series_id").agg(pl.col("week_start").max())["week_start"]
        earliest_end = cast(date, ends.min())
        h = (max(horizon) - earliest_end).days // 7
        model = self._factory()
        sf = StatsForecast(models=[model], freq=FREQ, n_jobs=1, fallback_model=Naive())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fc = sf.forecast(df=df, h=h, level=[80])
        col = type(model).__name__
        out = pl.from_pandas(fc.reset_index() if "unique_id" not in fc.columns else fc)
        out = out.select(
            pl.col("unique_id").cast(pl.String).alias("series_id"),
            pl.col("ds").cast(pl.Date).alias("week_start"),
            pl.col(f"{col}-lo-80").alias("lo"),
            pl.col(col).alias("mid"),
            pl.col(f"{col}-hi-80").alias("hi"),
        )
        out = out.filter(pl.col("week_start").is_in(list(horizon)))
        _check_horizon(out, horizon, history["series_id"].n_unique())
        mid = pl.col("mid").fill_nan(None).fill_null(0.0).clip(lower_bound=0.0)
        return out.select(
            "series_id",
            "week_start",
            pl.min_horizontal(pl.col("lo").fill_nan(None).clip(lower_bound=0.0), mid).alias("q10"),
            mid.alias("q50"),
            pl.max_horizontal(pl.col("hi").fill_nan(None).clip(lower_bound=0.0), mid).alias("q90"),
        )


def _check_horizon(out: pl.DataFrame, horizon: Sequence[date], n_series: int) -> None:
    if out.height != n_series * len(horizon):
        raise ValueError(
            f"statsforecast returned {out.height} rows for {n_series} series × {len(horizon)} weeks"
        )


def auto_ets(season_length: int = 52) -> StatsModel:
    return StatsModel("auto_ets", lambda: AutoETS(season_length=season_length))


def auto_theta(season_length: int = 52) -> StatsModel:
    return StatsModel("auto_theta", lambda: AutoTheta(season_length=season_length))


__all__ = ["StatsModel", "auto_ets", "auto_theta"]

"""Rolling-origin backtest harness (ARCH §5.4, PRD FR-7).

Series data is long: `series_id, week_start, y` (+ optional columns). A fold's `origin` is its
first forecast week; models only ever see rows with `week_start < origin`, plus known-future
covariates passed separately (e.g. planned spend).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

import polars as pl

QUANTILES = (0.1, 0.5, 0.9)
QCOLS = ("q10", "q50", "q90")


@dataclass(frozen=True)
class Fold:
    k: int
    origin: date  # first forecast week; all training rows are strictly earlier
    horizon: tuple[date, ...]

    @property
    def last_train_week(self) -> date:
        return self.origin - timedelta(weeks=1)


class Forecaster(Protocol):
    name: str

    def fit_predict(
        self,
        history: pl.DataFrame,
        horizon: Sequence[date],
        future: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        """Return `series_id, week_start, q10, q50, q90` for every series × horizon week."""
        ...


def rolling_origin_folds(
    first_week: date,
    last_week: date,
    *,
    n_folds: int = 6,
    horizon: int = 13,
    step: int = 4,
    min_train_weeks: int = 52,
) -> list[Fold]:
    """Folds whose horizons end at `last_week`, `last_week − step`, …; oldest first.

    Folds that would leave fewer than `min_train_weeks` of training history are dropped.
    """
    folds = []
    for i in range(n_folds):
        end = last_week - timedelta(weeks=step * i)
        origin = end - timedelta(weeks=horizon - 1)
        if (origin - first_week).days // 7 < min_train_weeks:
            break
        folds.append((origin, tuple(origin + timedelta(weeks=h) for h in range(horizon))))
    return [Fold(k, o, hz) for k, (o, hz) in enumerate(reversed(folds))]


def train_slice(data: pl.DataFrame, fold: Fold) -> pl.DataFrame:
    return data.filter(pl.col("week_start") < fold.origin)


def horizon_slice(data: pl.DataFrame, fold: Fold) -> pl.DataFrame:
    return data.filter(pl.col("week_start").is_in(fold.horizon))


def run_backtest(
    data: pl.DataFrame,
    models: Sequence[Forecaster],
    folds: Sequence[Fold],
    future: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Forecast every fold with every model; join actuals.

    Returns `model, fold, origin, h, series_id, week_start, y, q10, q50, q90`.
    `future` holds known-future covariates for all weeks; it is passed through untouched.
    """
    out = []
    for fold in folds:
        hist = train_slice(data, fold)
        actual = horizon_slice(data, fold).select("series_id", "week_start", "y")
        for model in models:
            pred = model.fit_predict(hist, fold.horizon, future)
            _check_prediction(pred, hist, fold, model.name)
            out.append(
                pred.join(actual, on=["series_id", "week_start"], how="inner").with_columns(
                    pl.lit(model.name).alias("model"),
                    pl.lit(fold.k).alias("fold"),
                    pl.lit(fold.origin).alias("origin"),
                    ((pl.col("week_start") - pl.lit(fold.origin)).dt.total_days() // 7 + 1)
                    .cast(pl.Int32)
                    .alias("h"),
                )
            )
    if not out:
        return pl.DataFrame()
    cols = ["model", "fold", "origin", "h", "series_id", "week_start", "y", *QCOLS]
    return pl.concat(out, how="vertical_relaxed").select(cols)


def _check_prediction(pred: pl.DataFrame, hist: pl.DataFrame, fold: Fold, name: str) -> None:
    missing = {"series_id", "week_start", *QCOLS} - set(pred.columns)
    if missing:
        raise ValueError(f"{name}: prediction missing columns {sorted(missing)}")
    if pred.filter(~pl.col("week_start").is_in(fold.horizon)).height:
        raise ValueError(f"{name}: prediction outside the fold horizon")
    expected = hist["series_id"].n_unique() * len(fold.horizon)
    if pred.height != expected:
        raise ValueError(f"{name}: expected {expected} rows, got {pred.height}")


# ---------------------------------------------------------------- metrics


def mase_scale(train_y: Sequence[float], seasonality: int = 52) -> float | None:
    """Mean absolute seasonal difference on training data; m=1 if too short; None if zero."""
    y = list(train_y)
    for m in (seasonality, 1):
        if len(y) > m:
            diffs = [abs(y[t] - y[t - m]) for t in range(m, len(y))]
            scale = sum(diffs) / len(diffs)
            if scale > 0:
                return scale
    return None


def pinball(y: pl.Expr, q_pred: pl.Expr, q: float) -> pl.Expr:
    diff = y - q_pred
    return pl.max_horizontal(q * diff, (q - 1) * diff)


def score_backtest(
    results: pl.DataFrame, data: pl.DataFrame, seasonality: int = 52
) -> pl.DataFrame:
    """Per model × series: MASE (on P50), mean pinball at 0.1/0.5/0.9, scaled pinball, coverage.

    The MASE scale for each fold uses only that fold's training data.
    """
    scales = []
    for (sid, origin), _ in results.group_by(["series_id", "origin"], maintain_order=True):
        y = (
            data.filter((pl.col("series_id") == sid) & (pl.col("week_start") < origin))
            .sort("week_start")["y"]
            .to_list()
        )
        scales.append({"series_id": sid, "origin": origin, "scale": mase_scale(y, seasonality)})
    scale_df = pl.DataFrame(
        scales, schema={"series_id": pl.String, "origin": pl.Date, "scale": pl.Float64}
    )
    r = results.join(scale_df, on=["series_id", "origin"], how="left").with_columns(
        (pl.col("y") - pl.col("q50")).abs().alias("ae"),
        pinball(pl.col("y"), pl.col("q10"), 0.1).alias("pb10"),
        pinball(pl.col("y"), pl.col("q50"), 0.5).alias("pb50"),
        pinball(pl.col("y"), pl.col("q90"), 0.9).alias("pb90"),
        pl.col("y").is_between(pl.col("q10"), pl.col("q90")).alias("covered"),
    )
    r = r.with_columns(
        pl.mean_horizontal("pb10", "pb50", "pb90").alias("pinball"),
        (pl.col("ae") / pl.col("scale")).alias("sae"),
    )
    return (
        r.group_by("model", "series_id")
        .agg(
            pl.col("sae").mean().alias("mase"),
            pl.col("pinball").mean().alias("pinball"),
            (pl.col("pinball") / pl.col("scale")).mean().alias("scaled_pinball"),
            pl.col("pb10").mean(),
            pl.col("pb50").mean(),
            pl.col("pb90").mean(),
            pl.col("covered").mean().alias("coverage"),
            pl.len().alias("n"),
        )
        .sort("series_id", "model")
    )

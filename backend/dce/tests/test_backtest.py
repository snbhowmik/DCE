"""T2.1: rolling-origin splitter (no leakage) and backtest metrics."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from itertools import pairwise

import polars as pl
import pytest

from dce.forecast.backtest import (
    Fold,
    mase_scale,
    rolling_origin_folds,
    run_backtest,
    score_backtest,
)

W0 = date(2024, 1, 1)


def weeks(n: int, start: date = W0) -> list[date]:
    return [start + timedelta(weeks=i) for i in range(n)]


def series(n: int = 110, sids: Sequence[str] = ("a", "b")) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series_id": [s for s in sids for _ in range(n)],
            "week_start": weeks(n) * len(sids),
            "y": [float(i % 7 + (10 if s == "b" else 0)) for s in sids for i in range(n)],
        }
    )


class SpyNaive:
    """Last-value forecaster that records exactly what it was shown."""

    name = "spy"

    def __init__(self) -> None:
        self.seen: list[tuple[date, date]] = []  # (max history week, first horizon week)

    def fit_predict(
        self, history: pl.DataFrame, horizon: Sequence[date], future: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        self.seen.append((history["week_start"].max(), horizon[0]))  # type: ignore[arg-type]
        last = history.sort("week_start").group_by("series_id").agg(pl.col("y").last())
        grid = last.join(pl.DataFrame({"week_start": list(horizon)}), how="cross")
        return grid.with_columns(
            (pl.col("y") - 1).alias("q10"), pl.col("y").alias("q50"), (pl.col("y") + 1).alias("q90")
        ).drop("y")


def test_folds_shape() -> None:
    first, last = W0, W0 + timedelta(weeks=109)
    folds = rolling_origin_folds(first, last, n_folds=6, horizon=13, step=4, min_train_weeks=52)
    assert len(folds) == 6
    assert [f.k for f in folds] == list(range(6))
    assert folds[-1].horizon[-1] == last
    assert all(len(f.horizon) == 13 for f in folds)
    origins = [f.origin for f in folds]
    assert all((b - a).days == 28 for a, b in pairwise(origins))
    assert all((f.origin - first).days // 7 >= 52 for f in folds)


def test_folds_dropped_when_history_short() -> None:
    folds = rolling_origin_folds(W0, W0 + timedelta(weeks=69), n_folds=6, min_train_weeks=52)
    assert len(folds) == 2  # 70 weeks: origins at week 57 and 53 only


def test_no_leakage_model_sees_only_pre_origin_rows() -> None:
    data = series()
    folds = rolling_origin_folds(W0, W0 + timedelta(weeks=109))
    spy = SpyNaive()
    res = run_backtest(data, [spy], folds)
    assert len(spy.seen) == len(folds)
    for (max_seen, first_h), fold in zip(spy.seen, folds, strict=True):
        assert max_seen < fold.origin == first_h
        assert max_seen == fold.last_train_week
    assert res["h"].min() == 1 and res["h"].max() == 13
    assert res.height == len(folds) * 13 * 2


def test_prediction_shape_is_enforced() -> None:
    class Bad:
        name = "bad"

        def fit_predict(self, history, horizon, future=None):
            return pl.DataFrame(
                {
                    "series_id": ["a"],
                    "week_start": [horizon[0]],
                    "q10": [0.0],
                    "q50": [0.0],
                    "q90": [0.0],
                }
            )

    fold = Fold(0, W0 + timedelta(weeks=60), tuple(weeks(13, W0 + timedelta(weeks=60))))
    with pytest.raises(ValueError, match="expected"):
        run_backtest(series(), [Bad()], [fold])


def test_mase_scale() -> None:
    assert mase_scale([1, 2, 3, 4], seasonality=52) == pytest.approx(1.0)  # fallback m=1
    y = [float(i % 52) for i in range(60)] + [100.0]  # m=52 diffs: eight 0s, then |100−8|
    assert mase_scale(y, 52) == pytest.approx(92 / 9)
    assert mase_scale([5.0] * 60, 52) is None  # constant → undefined, not inf


def test_metrics_hand_computed() -> None:
    origin = W0 + timedelta(weeks=4)
    data = pl.DataFrame(
        {
            "series_id": ["s"] * 6,
            "week_start": weeks(6),
            "y": [1.0, 2.0, 3.0, 4.0, 10.0, 10.0],  # train scale (m=1) = 1
        }
    )
    results = pl.DataFrame(
        {
            "model": ["m", "m"],
            "fold": [0, 0],
            "origin": [origin, origin],
            "h": [1, 2],
            "series_id": ["s", "s"],
            "week_start": [origin, origin + timedelta(weeks=1)],
            "y": [10.0, 10.0],
            "q10": [8.0, 11.0],
            "q50": [10.0, 12.0],
            "q90": [12.0, 13.0],
        }
    )
    s = score_backtest(results, data).row(0, named=True)
    assert s["mase"] == pytest.approx((0 + 2) / 2)
    # pb10: 0.1·2=0.2 | (0.1−1)(−1)=0.9 ; pb50: 0 | 0.5·2=1.0 ; pb90: 0.1·2=0.2 | 0.1·3=0.3
    assert s["pb10"] == pytest.approx((0.2 + 0.9) / 2)
    assert s["pb50"] == pytest.approx((0.0 + 1.0) / 2)
    assert s["pb90"] == pytest.approx((0.2 + 0.3) / 2)
    assert s["pinball"] == pytest.approx(((0.2 + 0 + 0.2) / 3 + (0.9 + 1.0 + 0.3) / 3) / 2)
    assert s["coverage"] == pytest.approx(0.5)


def test_constant_series_mase_is_null() -> None:
    data = pl.DataFrame({"series_id": ["c"] * 60, "week_start": weeks(60), "y": [3.0] * 60})
    folds = rolling_origin_folds(W0, W0 + timedelta(weeks=59), n_folds=1, min_train_weeks=40)
    scores = score_backtest(run_backtest(data, [SpyNaive()], folds), data)
    assert scores["mase"][0] is None
    assert scores["coverage"][0] == pytest.approx(1.0)

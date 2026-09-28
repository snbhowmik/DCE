"""T2.2: baseline point forecasts and empirical-residual quantiles."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.forecast.backtest import rolling_origin_folds, run_backtest, score_backtest
from dce.forecast.baselines import SeasonalNaive, WindowAverage

W0 = date(2024, 1, 1)


def frame(y: list[float], sid: str = "s") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series_id": [sid] * len(y),
            "week_start": [W0 + timedelta(weeks=i) for i in range(len(y))],
            "y": y,
        }
    )


def horizon(n_hist: int, h: int = 13) -> list[date]:
    return [W0 + timedelta(weeks=n_hist + i) for i in range(h)]


def test_seasonal_naive_copies_last_year() -> None:
    y = [float(i % 52) * 2 + 5 for i in range(104)]  # perfectly seasonal
    out = SeasonalNaive(52).fit_predict(frame(y), horizon(104))
    expected = [y[104 - 52 + h] for h in range(13)]
    assert out["q50"].to_list() == pytest.approx(expected)
    # zero seasonal residuals → zero-width band
    assert (out["q90"] - out["q10"]).abs().max() == pytest.approx(0.0)


def test_seasonal_naive_quantiles_from_residuals() -> None:
    rng = np.random.default_rng(1)
    y = list(100 + rng.normal(0, 10, 120))
    out = SeasonalNaive(52).fit_predict(frame(y), horizon(120))
    arr = np.array(y)
    r = arr[52:] - arr[:-52]
    q10, q90 = np.quantile(r, [0.1, 0.9])
    row = out.row(0, named=True)
    assert row["q10"] == pytest.approx(y[120 - 52] + q10)
    assert row["q90"] == pytest.approx(y[120 - 52] + q90)


def test_seasonal_naive_short_history_falls_back_to_last_value() -> None:
    y = [float(v) for v in range(30)]
    out = SeasonalNaive(52).fit_predict(frame(y), horizon(30))
    assert out["q50"].to_list() == pytest.approx([29.0] * 13)
    # h-step naive residuals on a unit trend are exactly h+1 → band sits above P50, widening
    assert out["q10"][0] == pytest.approx(29.0)  # all residuals > 0: P10 floored at P50
    assert out["q90"][0] > 29.0 and out["q90"][12] > out["q90"][0]


def test_window_average_point_and_residuals() -> None:
    y = [10.0] * 20 + [20.0] * 8
    out = WindowAverage(8).fit_predict(frame(y), horizon(28))
    assert out["q50"].to_list() == pytest.approx([20.0] * 13)
    # h=1 residuals: y[t+1] − mean(y[t−7..t]); the level shift gives positive residuals
    assert out["q90"][0] >= out["q50"][0] >= out["q10"][0]


def test_window_average_residuals_hand_computed() -> None:
    y = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0]
    out = WindowAverage(2).fit_predict(frame(y), horizon(12, 1))
    # h=1 residuals: y[i+2] − (y[i]+y[i+1])/2 = 3 for all 10 origins → P10 = P90 = 21 + 3
    assert out["q50"][0] == pytest.approx(21.0)
    assert out["q90"][0] == pytest.approx(24.0)
    assert out["q10"][0] == pytest.approx(21.0)  # residual P10 (+3) floored at P50


def test_quantiles_nonnegative_and_ordered() -> None:
    rng = np.random.default_rng(3)
    y = list(np.maximum(0, rng.normal(2, 5, 110)))
    for model in (SeasonalNaive(52), WindowAverage(8)):
        out = model.fit_predict(frame(y), horizon(110))
        assert (out["q10"] >= 0).all()
        assert (out["q10"] <= out["q50"]).all() and (out["q50"] <= out["q90"]).all()


def test_baselines_run_in_backtest(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code-path smoke on the fixture; no accuracy is asserted or reported."""
    from dce.demand.reconstruct import weekly_demand

    d = weekly_demand(tiny_tables["orders"], tiny_window[1]).select(
        "series_id", "week_start", pl.col("demand_kg").alias("y")
    )
    folds = rolling_origin_folds(*tiny_window)
    res = run_backtest(d, [SeasonalNaive(52), WindowAverage(8)], folds)
    scores = score_backtest(res, d)
    assert set(scores["model"]) == {"seasonal_naive_52", "window_average_8"}
    assert scores["coverage"].is_between(0, 1).all()

"""T2.3: AutoETS / AutoTheta wrappers honor the Forecaster contract."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.forecast.backtest import rolling_origin_folds, run_backtest, score_backtest
from dce.forecast.statistical import StatsModel, auto_ets, auto_theta

W0 = date(2024, 1, 1)


def frame(ys: dict[str, list[float]]) -> pl.DataFrame:
    return pl.concat(
        pl.DataFrame(
            {
                "series_id": [sid] * len(y),
                "week_start": [W0 + timedelta(weeks=i) for i in range(len(y))],
                "y": y,
            }
        )
        for sid, y in ys.items()
    )


@pytest.mark.parametrize("model", [auto_ets(), auto_theta()], ids=lambda m: m.name)
def test_contract_shape_and_order(model: StatsModel) -> None:
    rng = np.random.default_rng(0)
    hist = frame(
        {
            "level": list(50 + rng.normal(0, 3, 110)),
            "trend": list(np.arange(110) * 0.5 + rng.normal(0, 1, 110)),
            "zeros": [0.0] * 110,  # degenerate: must not crash
            "ended": [3.0, 4.0, 5.0, 4.0, 6.0],  # stops at week 5: forecast through the gap
        }
    )
    horizon = [W0 + timedelta(weeks=110 + h) for h in range(13)]
    out = model.fit_predict(hist, horizon)
    assert out.height == 4 * 13
    assert set(out["week_start"].unique()) == set(horizon)
    assert (out["q10"] <= out["q50"]).all() and (out["q50"] <= out["q90"]).all()
    assert (out["q10"] >= 0).all()
    assert out.select(pl.col("q10", "q50", "q90").is_nan().any()).row(0) == (False, False, False)
    lvl = out.filter(pl.col("series_id") == "level")
    assert 40 < float(lvl["q50"].mean()) < 60  # type: ignore[arg-type]


def test_trend_is_extrapolated_by_ets() -> None:
    hist = frame({"t": [float(i) for i in range(110)]})
    out = auto_ets().fit_predict(hist, [W0 + timedelta(weeks=110 + h) for h in range(13)])
    assert out["q50"][12] > out["q50"][0] > 100


def test_models_run_in_backtest(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code-path smoke on the fixture; no accuracy asserted."""
    from dce.demand.reconstruct import weekly_demand

    d = weekly_demand(tiny_tables["orders"], tiny_window[1]).select(
        "series_id", "week_start", pl.col("demand_kg").alias("y")
    )
    folds = rolling_origin_folds(*tiny_window)[-2:]
    scores = score_backtest(run_backtest(d, [auto_ets(), auto_theta()], folds), d)
    assert set(scores["model"]) == {"auto_ets", "auto_theta"}

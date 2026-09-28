"""T2.5: an injected spike is flagged, winsorized for training, and not in forward P50."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.config import load_scoring_config
from dce.demand.reconstruct import weekly_demand
from dce.forecast.anomaly import AnomalyConfig, anomaly_report, detect_anomalies
from dce.forecast.backtest import Forecaster
from dce.forecast.baselines import SeasonalNaive, WindowAverage
from dce.forecast.covariates import build_covariates, series_meta
from dce.forecast.lgbm import LightGBMQuantile

SERIES = "D2C|R_N|SKU_CC_500"


@pytest.fixture(scope="module")
def spiked(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> tuple[pl.DataFrame, list[date]]:
    demand = weekly_demand(tiny_tables["orders"], tiny_window[1]).rename({"demand_kg": "y"})
    last = tiny_window[1]
    # last−1 feeds lag/window rules; last−50 is the seasonal source for horizon week +2
    spike_weeks = [last - timedelta(weeks=1), last - timedelta(weeks=50)]
    d = demand.with_columns(
        pl.when((pl.col("series_id") == SERIES) & pl.col("week_start").is_in(spike_weeks))
        .then(pl.col("y") * 8)
        .otherwise(pl.col("y"))
        .alias("y")
    )
    return d, spike_weeks


def test_injected_spike_flagged_and_winsorized(spiked: tuple[pl.DataFrame, list[date]]) -> None:
    d, spike_weeks = spiked
    out = detect_anomalies(d, AnomalyConfig.from_scoring(load_scoring_config()))
    flags = out.filter(pl.col("is_anomaly"))
    hit = flags.filter(pl.col("series_id") == SERIES)["week_start"].to_list()
    assert set(spike_weeks) <= set(hit)
    row = out.filter((pl.col("series_id") == SERIES) & (pl.col("week_start") == spike_weeks[0]))
    assert row["y_clean"][0] < row["y"][0] / 3
    # the fixture's stockout dip is censoring, not an anomaly
    assert (
        not flags.filter(pl.col("robust_z") < 0)["week_start"]
        .is_in(d.filter(pl.col("is_censored"))["week_start"].implode())
        .any()
    )
    # B2B is out of scope by default
    assert not flags["channel"].is_in(["B2B"]).any()
    assert set(anomaly_report(out).columns) >= {"series_id", "week_start", "robust_z"}


@pytest.mark.parametrize(
    "model", [SeasonalNaive(52), WindowAverage(8), LightGBMQuantile()], ids=lambda m: m.name
)
def test_spike_not_in_forward_p50(
    model: Forecaster,
    spiked: tuple[pl.DataFrame, list[date]],
    tiny_tables: dict[str, pl.DataFrame],
    tiny_window: tuple[date, date],
) -> None:
    d, _ = spiked
    first, last = tiny_window
    clean = detect_anomalies(d).select("series_id", "week_start", pl.col("y_clean").alias("y"))
    raw = d.select("series_id", "week_start", "y")
    horizon = [last + timedelta(weeks=h) for h in range(1, 14)]
    cov = build_covariates(tiny_tables, series_meta(d), first, horizon[-1])

    def p50(hist: pl.DataFrame) -> float:
        out = model.fit_predict(hist, horizon, cov)
        return float(out.filter(pl.col("series_id") == SERIES)["q50"].max())  # type: ignore[arg-type]

    level = float(
        raw.filter(
            (pl.col("series_id") == SERIES) & (pl.col("week_start") < last - timedelta(weeks=1))
        )
        .tail(12)["y"]
        .mean()  # type: ignore[arg-type]
    )
    assert p50(clean) < 1.6 * level  # the spike does not propagate
    if model.name != "lightgbm":  # the raw spike does leak into naive rules (sanity check)
        assert p50(raw) > p50(clean)


def test_constant_and_zero_series_never_flagged() -> None:
    wk = [date(2024, 1, 1) + timedelta(weeks=i) for i in range(60)]
    d = pl.DataFrame(
        {
            "series_id": ["a"] * 60 + ["b"] * 60,
            "week_start": wk * 2,
            "channel": ["D2C"] * 120,
            "y": [5.0] * 60 + [0.0] * 60,
        }
    )
    out = detect_anomalies(d)
    assert not out["is_anomaly"].any()
    assert out["y_clean"].to_list() == out["y"].to_list()


def test_noise_rarely_flagged() -> None:
    rng = np.random.default_rng(0)
    wk = [date(2024, 1, 1) + timedelta(weeks=i) for i in range(500)]
    d = pl.DataFrame(
        {"series_id": ["n"] * 500, "week_start": wk, "channel": ["D2C"] * 500,
         "y": list(100 + rng.normal(0, 5, 500))}
    )  # fmt: skip
    assert detect_anomalies(d)["is_anomaly"].mean() < 0.01  # type: ignore[operator]

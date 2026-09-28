"""T2.7: B2B forecasting via order-to-commitment ratios."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from dce.demand.reconstruct import weekly_demand
from dce.forecast.b2b import B2BForecast, account_eligibility, forecast_b2b
from dce.forecast.pipeline import ForecastConfig

CFG = ForecastConfig(n_paths=100, models=("seasonal_naive_52", "window_average_8", "auto_theta"))


def _run(tables: dict[str, pl.DataFrame], window: tuple[date, date]) -> B2BForecast:
    demand = weekly_demand(tables["orders"], window[1])
    return forecast_b2b(tables, demand, window, CFG, seed=7)


@pytest.fixture(scope="module")
def base(tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]) -> B2BForecast:
    return _run(tiny_tables, tiny_window)


def test_eligibility_and_reasons(base: B2BForecast) -> None:
    acc = {r["account_id"]: r for r in base.accounts.iter_rows(named=True)}
    assert acc["ACC_DIST"]["included"] and acc["ACC_REST"]["included"]
    assert not acc["ACC_QSR"]["included"] and "pipeline" in acc["ACC_QSR"]["reason"]
    assert set(base.quantiles_kg["account_id"]) == {"ACC_DIST", "ACC_REST"}
    assert base.paths_kg.shape == (2, 100, 13)


def test_ratio_distribution_per_account(base: B2BForecast) -> None:
    d = base.ratio_distribution
    assert set(d["account_id"]) == {"ACC_DIST", "ACC_REST"}
    for c in ("ratio_mean", "ratio_p10", "ratio_p50", "ratio_p90", "monthly_ratio_mean", "n_weeks"):
        assert d[c].is_not_null().all(), c
    assert (d["ratio_p10"] <= d["ratio_p90"]).all()


def test_kg_is_ratio_times_commitment(base: B2BForecast) -> None:
    commit = 400 / (52 / 12)  # ACC_DIST
    assert base.ratio_forecast is not None
    ratio = base.ratio_forecast.quantiles.filter(pl.col("series_id") == "B2B|ACC_DIST")
    kg = base.quantiles_kg.filter(pl.col("account_id") == "ACC_DIST")
    assert kg["q50"].to_numpy() == pytest.approx(ratio["q50"].to_numpy() * commit)


@pytest.mark.parametrize("status", ["churned", "paused"])
def test_churned_and_paused_excluded(
    status: str, tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    tables = dict(tiny_tables)
    tables["b2b_accounts"] = tiny_tables["b2b_accounts"].with_columns(
        pl.when(pl.col("account_id") == "ACC_REST")
        .then(pl.lit(status))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    out = _run(tables, tiny_window)
    assert set(out.quantiles_kg["account_id"]) == {"ACC_DIST"}
    rest = out.accounts.filter(pl.col("account_id") == "ACC_REST").row(0, named=True)
    assert rest["reason"] == status


def test_contract_end_respected(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    end = tiny_window[1] + timedelta(weeks=5, days=2)  # mid-horizon
    tables = dict(tiny_tables)
    tables["b2b_accounts"] = tiny_tables["b2b_accounts"].with_columns(
        pl.when(pl.col("account_id") == "ACC_REST")
        .then(pl.lit(end))
        .otherwise(pl.col("contract_end"))
        .alias("contract_end")
    )
    out = _run(tables, tiny_window)
    kg = out.quantiles_kg.filter(pl.col("account_id") == "ACC_REST")
    after = kg.filter(pl.col("week_start") > end)
    before = kg.filter(pl.col("week_start") <= end)
    assert after.height == 8 and (after["q90"] == 0).all()
    assert (before["q50"] > 0).all()
    i = out.path_series.index("B2B|ACC_REST")
    assert (out.paths_kg[i, :, 5:] == 0).all() and (out.paths_kg[i, :, :5] > 0).any()


def test_expired_contract_excluded() -> None:
    accts = pl.DataFrame(
        {
            "account_id": ["X"], "region_id": ["R"], "status": ["active"],
            "contract_start": [date(2024, 1, 1)], "contract_end": [date(2024, 6, 30)],
            "committed_kg_per_month": [100.0],
        }
    )  # fmt: skip
    e = account_eligibility(accts, date(2025, 1, 6)).row(0, named=True)
    assert not e["included"] and "ended" in e["reason"]


def test_young_account_gets_baseline_and_spread(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    start = tiny_window[1] - timedelta(weeks=8)
    tables = dict(tiny_tables)
    tables["b2b_accounts"] = tiny_tables["b2b_accounts"].with_columns(
        pl.when(pl.col("account_id") == "ACC_REST")
        .then(pl.lit(start))
        .otherwise(pl.col("contract_start"))
        .alias("contract_start")
    )
    out = _run(tables, tiny_window)
    assert out.ratio_forecast is not None
    sel = out.ratio_forecast.selection.filter(pl.col("series_id") == "B2B|ACC_REST").row(
        0, named=True
    )
    assert sel["model"] == "seasonal_naive_52"
    i = out.path_series.index("B2B|ACC_REST")
    assert np.std(out.paths_kg[i, :, 0]) > 0


def test_dataset_forecast_combines_channels(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date], tmp_path: Path
) -> None:
    from dce.forecast.run import forecast_dataset

    app = {
        "horizon_weeks": 13,
        "n_paths": 50,
        "forecast": {"models": ["seasonal_naive_52", "window_average_8"]},
    }
    fc = forecast_dataset(tiny_tables, tiny_window, app, {}, seed=5)
    q = fc.quantiles()
    assert set(q["channel"]) == {"D2C", "B2B"}
    assert q.filter(pl.col("channel") == "B2B")["account_id"].is_not_null().all()
    sids, arr = fc.paths()
    assert arr.shape == (len(sids), 50, 13) and len(sids) == q["series_id"].n_unique()
    assert (
        fc.artifact_hash()
        == forecast_dataset(tiny_tables, tiny_window, app, {}, seed=5).artifact_hash()
    )
    files = fc.write(tmp_path)
    assert all(p.is_file() for p in files.values())

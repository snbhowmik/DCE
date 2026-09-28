"""T2.4: LightGBM quantile model: no leakage, uses planned spend, no crossing."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from dce.forecast.covariates import build_covariates, series_meta
from dce.forecast.lgbm import FEATURES, LgbmConfig, LightGBMQuantile, _features, _panel, adstock

W0 = date(2024, 1, 1)
N = 110
H = 13


def weeks(n: int, start: date = W0) -> list[date]:
    return [start + timedelta(weeks=i) for i in range(n)]


def synthetic(spend_future: float = 1000.0, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Demand driven by adstocked planned spend; returns (history, covariates incl. horizon)."""
    rng = np.random.default_rng(seed)
    sids = [f"D2C|R{i}|S" for i in range(4)]
    spend = np.zeros((4, N + H))
    for i in range(4):
        level = 1000.0
        for t in range(N + H):
            if t % 15 == 0:
                level = float(rng.choice([500.0, 1000.0, 3000.0, 6000.0]))
            spend[i, t] = level
    spend[:, N:] = spend_future
    ad = adstock(spend, 0.5)
    y = 20 + 0.02 * ad + rng.normal(0, 1.0, (4, N + H))
    hist = pl.DataFrame(
        {
            "series_id": [s for s in sids for _ in range(N)],
            "week_start": weeks(N) * 4,
            "y": y[:, :N].ravel(),
        }
    )
    cov = pl.DataFrame(
        {
            "series_id": [s for s in sids for _ in range(N + H)],
            "week_start": weeks(N + H) * 4,
            "planned_spend_inr": spend.ravel(),
            "tier": ["metro"] * (4 * (N + H)),
            "channel": ["D2C"] * (4 * (N + H)),
            "price_inr": [1500.0] * (4 * (N + H)),
            "holiday_count": [0] * (4 * (N + H)),
            "festival_week": [False] * (4 * (N + H)),
        }
    )
    return hist, cov


HORIZON = weeks(H, W0 + timedelta(weeks=N))


def test_features_ignore_everything_after_origin() -> None:
    hist, cov = synthetic()
    p = _panel(hist, HORIZON, cov, 0.5)
    rng = np.random.default_rng(1)
    for o in (20, 50, 90):
        poisoned = _panel(hist, HORIZON, cov, 0.5)
        poisoned.Y[:, o + 1 :] = rng.normal(1e6, 1e5, poisoned.Y[:, o + 1 :].shape)
        for h in (1, 5, 13):
            a, sa = _features(p, o, h, 52)
            b, sb = _features(poisoned, o, h, 52)
            np.testing.assert_array_equal(a, b)
            np.testing.assert_array_equal(sa, sb)


def test_training_rows_have_targets_after_origin_only() -> None:
    hist, cov = synthetic()
    model = LightGBMQuantile()
    p = _panel(hist, HORIZON, cov, 0.5)
    X, _, origins = model.training_set(p, H)
    assert X.shape[1] == len(FEATURES)
    h_col = X[:, FEATURES.index("h")]
    assert (origins + h_col <= p.T - 1).all()  # every target is inside history
    assert (h_col >= 1).all()


def test_planned_spend_moves_the_forecast() -> None:
    low_hist, low_cov = synthetic(spend_future=500.0)
    _, high_cov = synthetic(spend_future=6000.0)
    model = LightGBMQuantile(LgbmConfig(seed=3))
    low = model.fit_predict(low_hist, HORIZON, low_cov)
    high = model.fit_predict(low_hist, HORIZON, high_cov)  # same history, different plan
    late = pl.col("week_start") >= HORIZON[4]
    assert high.filter(late)["q50"].mean() > low.filter(late)["q50"].mean() * 1.3  # type: ignore[operator]


def test_no_crossing_nonnegative_and_deterministic() -> None:
    hist, cov = synthetic()
    model = LightGBMQuantile()
    a = model.fit_predict(hist, HORIZON, cov)
    b = model.fit_predict(hist, HORIZON, cov)
    assert a.equals(b)
    assert a.height == 4 * H
    assert (a["q10"] <= a["q50"]).all() and (a["q50"] <= a["q90"]).all()
    assert (a["q10"] >= 0).all()


def test_falls_back_when_too_little_data() -> None:
    hist = pl.DataFrame({"series_id": ["s"] * 20, "week_start": weeks(20), "y": [5.0] * 20})
    out = LightGBMQuantile().fit_predict(hist, weeks(H, W0 + timedelta(weeks=20)))
    assert out.height == H and out["q50"].to_list() == pytest.approx([5.0] * H)


def test_horizon_must_follow_history() -> None:
    hist, cov = synthetic()
    with pytest.raises(ValueError, match="horizon"):
        LightGBMQuantile().fit_predict(hist, weeks(H, W0 + timedelta(weeks=N + 2)), cov)


def test_covariates_never_read_realized_data(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    from dce.demand.reconstruct import weekly_demand

    first, last = tiny_window
    meta = series_meta(weekly_demand(tiny_tables["orders"], last))
    end = last + timedelta(weeks=H)
    base = build_covariates(tiny_tables, meta, first, end)
    tampered = dict(tiny_tables)
    tampered["marketing_daily"] = tiny_tables["marketing_daily"].with_columns(
        pl.col("spend_inr") * 100
    )
    tampered["orders"] = tiny_tables["orders"].with_columns(pl.col("requested_qty_kg") * 7)
    assert build_covariates(tampered, meta, first, end).equals(base)
    # ... while the plan does flow through
    replanned = dict(tiny_tables)
    replanned["marketing_plan"] = tiny_tables["marketing_plan"].with_columns(
        pl.col("planned_spend_inr") * 2
    )
    assert not build_covariates(replanned, meta, first, end).equals(base)
    assert base["week_start"].max() == end
    assert base.filter(pl.col("week_start") > last)["planned_spend_inr"].sum() > 0


def test_lightgbm_runs_in_backtest_with_covariates(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Code-path smoke on the fixture; no accuracy asserted."""
    from dce.demand.reconstruct import weekly_demand
    from dce.forecast.backtest import rolling_origin_folds, run_backtest, score_backtest

    first, last = tiny_window
    demand = weekly_demand(tiny_tables["orders"], last)
    d = demand.select("series_id", "week_start", pl.col("demand_kg").alias("y"))
    cov = build_covariates(tiny_tables, series_meta(demand), first, last + timedelta(weeks=H))
    folds = rolling_origin_folds(first, last)[-2:]
    scores = score_backtest(run_backtest(d, [LightGBMQuantile()], folds, cov), d)
    assert scores["model"].unique().to_list() == ["lightgbm"]
    assert scores["coverage"].is_between(0, 1).all()

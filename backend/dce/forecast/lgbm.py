"""LightGBM global quantile model, direct multi-horizon (ARCH §5.4, T2.4).

Each training row is (series, origin o, step h) with target y[o+h]. Every history-derived feature
comes from weeks ≤ o; only known-future covariates (planned spend, calendar, static attributes)
are read at the target week. Values are normalized by the series' recent level so one model
serves all regions/accounts. One LightGBM model per quantile; outputs are sorted per row (no
crossing) and clipped at 0.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import lightgbm as lgb
import numpy as np
import polars as pl

from dce.forecast.backtest import PREDICTION_SCHEMA, QUANTILES, empty_prediction
from dce.forecast.baselines import WindowAverage
from dce.logs import get_logger
from dce.seeds import derive_seed

log = get_logger(__name__)

FEATURES = (
    "h", "lag_1", "lag_2", "lag_3", "lag_4", "lag_8", "lag_13", "seasonal_52",
    "roll_mean_4", "roll_std_4", "roll_mean_13", "roll_std_13", "log_scale",
    "woy_sin", "woy_cos", "month", "holiday_count", "festival_week",
    "tier", "channel", "log_price", "log_adstock_t", "log_adstock_o", "adstock_ratio",
)  # fmt: skip
LAGS = (1, 2, 3, 4, 8, 13)


@dataclass(frozen=True)
class LgbmConfig:
    seasonal_lag: int = 52
    adstock_theta: float = 0.5
    min_origin: int = 13
    min_train_rows: int = 200
    n_estimators: int = 250
    learning_rate: float = 0.05
    num_leaves: int = 15
    min_child_samples: int = 20
    subsample: float = 0.8
    colsample_bytree: float = 0.9
    seed: int = 0


def adstock(x: np.ndarray, theta: float) -> np.ndarray:
    """Geometric adstock along the last axis: a_t = x_t + θ·a_{t−1}."""
    out = np.zeros_like(x, dtype=float)
    acc = np.zeros(x.shape[:-1])
    for t in range(x.shape[-1]):
        acc = x[..., t] + theta * acc
        out[..., t] = acc
    return out


@dataclass
class _Panel:
    sids: list[str]
    weeks: list[date]  # history weeks then horizon weeks
    T: int  # number of history weeks
    Y: np.ndarray  # [S, T], NaN before a series starts
    spend: np.ndarray  # [S, T+H] adstocked planned spend
    tier: np.ndarray  # [S] category code
    channel: np.ndarray  # [S] 0 = D2C, 1 = B2B
    log_price: np.ndarray  # [S]
    woy: np.ndarray  # [T+H]
    month: np.ndarray  # [T+H]
    holidays: np.ndarray  # [T+H]
    festival: np.ndarray  # [T+H]


def _panel(
    history: pl.DataFrame,
    horizon: Sequence[date],
    future: pl.DataFrame | None,
    theta: float,
) -> _Panel:
    first = history["week_start"].min()
    last = history["week_start"].max()
    assert isinstance(first, date) and isinstance(last, date)
    if min(horizon) != last + timedelta(weeks=1):
        raise ValueError("horizon must start the week after the last history week")
    T = (last - first).days // 7 + 1
    weeks = [first + timedelta(weeks=i) for i in range(T + len(horizon))]
    sids = sorted(history["series_id"].unique().to_list())
    s_idx = {s: i for i, s in enumerate(sids)}
    Y = np.full((len(sids), T), np.nan)
    for sid, wk, y in history.select("series_id", "week_start", "y").iter_rows():
        Y[s_idx[sid], (wk - first).days // 7] = y

    S, TH = len(sids), len(weeks)
    spend = np.zeros((S, TH))
    tier = np.zeros(S)
    channel = np.zeros(S)
    log_price = np.full(S, np.nan)
    holidays_ = np.zeros(TH)
    festival = np.zeros(TH)
    if future is not None and not future.is_empty():
        f = future.filter(
            pl.col("series_id").is_in(sids) & pl.col("week_start").is_between(first, weeks[-1])
        )
        tiers = sorted(f["tier"].unique().to_list())
        t_idx = {t: i for i, t in enumerate(tiers)}
        for sid, wk, sp in f.select("series_id", "week_start", "planned_spend_inr").iter_rows():
            spend[s_idx[sid], (wk - first).days // 7] = sp
        static = f.group_by("series_id").agg(pl.col("tier", "channel", "price_inr").first())
        for sid, tr, ch, pr in static.iter_rows():
            tier[s_idx[sid]] = t_idx[tr]
            channel[s_idx[sid]] = 1.0 if ch == "B2B" else 0.0
            log_price[s_idx[sid]] = np.log1p(pr) if pr is not None else np.nan
        cal = f.group_by("week_start").agg(pl.col("holiday_count", "festival_week").first())
        for wk, hc, fw in cal.iter_rows():
            i = (wk - first).days // 7
            holidays_[i], festival[i] = hc, float(fw)
    woy = np.array([w.isocalendar()[1] for w in weeks], dtype=float)
    month = np.array([w.month for w in weeks], dtype=float)
    return _Panel(
        sids, weeks, T, Y, adstock(spend, theta), tier, channel, log_price,
        woy, month, holidays_, festival,
    )  # fmt: skip


def _features(p: _Panel, o: int, h: int, seasonal_lag: int) -> tuple[np.ndarray, np.ndarray]:
    """Feature matrix [S, F] for origin index o and step h, and the per-series scale [S]."""
    t = o + h
    Y = p.Y
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows → NaN, handled below
        scale = np.nanmean(Y[:, max(0, o - 12) : o + 1], axis=1) + 1.0
        w4, w13 = Y[:, max(0, o - 3) : o + 1], Y[:, max(0, o - 12) : o + 1]
        rm4, rs4 = np.nanmean(w4, axis=1), np.nanstd(w4, axis=1)
        rm13, rs13 = np.nanmean(w13, axis=1), np.nanstd(w13, axis=1)
    lags = [Y[:, o - k + 1] if o - k + 1 >= 0 else np.full(len(Y), np.nan) for k in LAGS]
    s_src = t - seasonal_lag
    seasonal = Y[:, s_src] if 0 <= s_src <= o else np.full(len(Y), np.nan)
    ad_t, ad_o = p.spend[:, t], p.spend[:, o]
    woy = p.woy[t]
    cols = [
        np.full(len(Y), h, dtype=float),
        *(lag / scale for lag in lags),
        seasonal / scale,
        rm4 / scale, rs4 / scale, rm13 / scale, rs13 / scale,
        np.log(scale),
        np.full(len(Y), np.sin(2 * np.pi * woy / 52.18)),
        np.full(len(Y), np.cos(2 * np.pi * woy / 52.18)),
        np.full(len(Y), p.month[t]),
        np.full(len(Y), p.holidays[t]),
        np.full(len(Y), p.festival[t]),
        p.tier, p.channel, p.log_price,
        np.log1p(ad_t), np.log1p(ad_o), (ad_t + 1.0) / (ad_o + 1.0),
    ]  # fmt: skip
    return np.column_stack(cols), scale


class LightGBMQuantile:
    name = "lightgbm"

    def __init__(self, cfg: LgbmConfig | None = None) -> None:
        self.cfg = cfg or LgbmConfig()

    def training_set(self, p: _Panel, H: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """X, y (normalized), and the origin index of each row (for leakage tests)."""
        xs, ys, origins = [], [], []
        for o in range(self.cfg.min_origin, p.T - 1):
            for h in range(1, H + 1):
                if o + h > p.T - 1:
                    break
                X, scale = _features(p, o, h, self.cfg.seasonal_lag)
                y = p.Y[:, o + h] / scale
                ok = np.isfinite(y) & np.isfinite(scale)
                xs.append(X[ok])
                ys.append(y[ok])
                origins.append(np.full(ok.sum(), o))
        if not xs:
            return np.empty((0, len(FEATURES))), np.empty(0), np.empty(0)
        return np.vstack(xs), np.concatenate(ys), np.concatenate(origins)

    def fit_predict(
        self, history: pl.DataFrame, horizon: Sequence[date], future: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        if history.is_empty():
            return empty_prediction()
        H = len(horizon)
        p = _panel(history, horizon, future, self.cfg.adstock_theta)
        X, y, _ = self.training_set(p, H)
        if len(y) < self.cfg.min_train_rows:
            log.warning("lightgbm_fallback", rows=len(y), reason="too few training rows")
            return WindowAverage(8).fit_predict(history, horizon, future)

        seed = derive_seed(self.cfg.seed, "lightgbm", p.weeks[p.T - 1])
        preds = []
        for q in QUANTILES:
            params = {
                "objective": "quantile",
                "alpha": q,
                "learning_rate": self.cfg.learning_rate,
                "num_leaves": self.cfg.num_leaves,
                "min_data_in_leaf": self.cfg.min_child_samples,
                "bagging_fraction": self.cfg.subsample,
                "bagging_freq": 1,
                "feature_fraction": self.cfg.colsample_bytree,
                "seed": seed % (2**31),
                "deterministic": True,
                "force_row_wise": True,
                "num_threads": 1,
                "verbose": -1,
            }
            model = lgb.train(params, lgb.Dataset(X, y, free_raw_data=True), self.cfg.n_estimators)
            rows = []
            for h in range(1, H + 1):
                Xh, scale = _features(p, p.T - 1, h, self.cfg.seasonal_lag)
                rows.append(model.predict(Xh) * scale)
            preds.append(np.column_stack(rows))  # [S, H]
        Q = np.sort(np.stack(preds, axis=-1), axis=-1)  # [S, H, 3], no crossing
        Q = np.nan_to_num(np.maximum(Q, 0.0))
        out = [
            (sid, horizon[h], *Q[i, h].tolist()) for i, sid in enumerate(p.sids) for h in range(H)
        ]
        return pl.DataFrame(out, schema=PREDICTION_SCHEMA, orient="row")

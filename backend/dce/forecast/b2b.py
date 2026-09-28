"""B2B account forecasting via the order-to-commitment ratio (ARCH §5.4, T2.7).

For each active account with a commitment:
  ratio_t   = weekly ordered kg ÷ weekly commitment (commitment/month × 12/52)
  forecast  = the standard pipeline on ratio series (selection, calibration, paths)
  kg        = ratio × weekly commitment, 0 for weeks starting after `contract_end`

Churned, paused and pipeline accounts are not forecast (pipeline accounts enter only via the
onboarding simulator or scenarios). Contracts are not assumed to renew (A-010).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

from dce.forecast.covariates import build_covariates
from dce.forecast.pipeline import ForecastConfig, ForecastSet, run_forecast

WEEKS_PER_MONTH = 52 / 12


def b2b_series_id(account_id: str) -> str:
    return f"B2B|{account_id}"


def account_eligibility(accounts: pl.DataFrame, first_horizon_week: date) -> pl.DataFrame:
    """`account_id, included, reason, weekly_commit_kg, …` for every account."""
    reason = (
        pl.when(pl.col("status") == "pipeline")
        .then(pl.lit("pipeline: onboarding simulator only"))
        .when(pl.col("status") == "churned")
        .then(pl.lit("churned"))
        .when(pl.col("status") == "paused")
        .then(pl.lit("paused"))
        .when(pl.col("committed_kg_per_month").is_null() | (pl.col("committed_kg_per_month") <= 0))
        .then(pl.lit("no commitment"))
        .when(pl.col("contract_start").is_null())
        .then(pl.lit("no contract start"))
        .when(pl.col("contract_end").is_not_null() & (pl.col("contract_end") < first_horizon_week))
        .then(pl.lit("contract ended before horizon"))
        .otherwise(None)
    )
    return accounts.select(
        "account_id",
        "region_id",
        "status",
        "contract_start",
        "contract_end",
        "committed_kg_per_month",
        (pl.col("committed_kg_per_month") / WEEKS_PER_MONTH).alias("weekly_commit_kg"),
        reason.alias("reason"),
    ).with_columns(pl.col("reason").is_null().alias("included"))


def ratio_series(
    demand: pl.DataFrame, eligible: pl.DataFrame, first: date, last: date
) -> pl.DataFrame:
    """Weekly ratio per included account from contract start (or history start) to `last`."""
    acc = eligible.filter(pl.col("included"))
    weekly = (
        demand.filter(pl.col("channel") == "B2B")
        .group_by("account_id", "week_start")
        .agg(pl.col("demand_kg").sum(), pl.col("is_censored").any())
    )
    grid = acc.select(
        "account_id",
        "weekly_commit_kg",
        pl.max_horizontal(pl.col("contract_start").dt.truncate("1w"), pl.lit(first)).alias("s"),
    ).with_columns(pl.date_ranges("s", pl.lit(last), interval="1w").alias("week_start"))
    grid = grid.explode("week_start", empty_as_null=False).drop_nulls("week_start").drop("s")
    return (
        grid.join(weekly, on=["account_id", "week_start"], how="left")
        .with_columns(
            pl.col("demand_kg").fill_null(0.0),
            pl.col("is_censored").fill_null(False),
        )
        .with_columns(
            (pl.col("demand_kg") / pl.col("weekly_commit_kg")).alias("ratio"),
            pl.col("account_id")
            .map_elements(b2b_series_id, return_dtype=pl.String)
            .alias("series_id"),
        )
        .sort("account_id", "week_start")
    )


def ratio_distribution(ratios: pl.DataFrame) -> pl.DataFrame:
    """Per-account empirical distribution of weekly and monthly order-to-commitment ratios."""
    monthly = (
        ratios.with_columns(pl.col("week_start").dt.truncate("1mo").alias("m"))
        .group_by("account_id", "m")
        .agg(
            pl.col("demand_kg").sum(),
            (pl.col("weekly_commit_kg").first() * pl.len()).alias("commit"),
        )
        .with_columns((pl.col("demand_kg") / pl.col("commit")).alias("mratio"))
        .group_by("account_id")
        .agg(
            pl.col("mratio").mean().alias("monthly_ratio_mean"),
            pl.col("mratio").std().alias("monthly_ratio_sd"),
        )
    )
    weekly = ratios.group_by("account_id").agg(
        pl.len().alias("n_weeks"),
        pl.col("ratio").mean().alias("ratio_mean"),
        pl.col("ratio").std().alias("ratio_sd"),
        pl.col("ratio").quantile(0.1).alias("ratio_p10"),
        pl.col("ratio").quantile(0.5).alias("ratio_p50"),
        pl.col("ratio").quantile(0.9).alias("ratio_p90"),
    )
    return weekly.join(monthly, on="account_id", how="left").sort("account_id")


@dataclass
class B2BForecast:
    accounts: pl.DataFrame  # eligibility incl. excluded accounts with reasons
    ratio_history: pl.DataFrame
    ratio_distribution: pl.DataFrame
    ratio_forecast: ForecastSet | None
    quantiles_kg: pl.DataFrame  # series_id, account_id, week_start, h, q10, q50, q90
    path_series: list[str]
    paths_kg: np.ndarray  # [A, P, H]


def forecast_b2b(
    tables: dict[str, pl.DataFrame],
    demand: pl.DataFrame,
    window: tuple[date, date],
    cfg: ForecastConfig,
    seed: int,
) -> B2BForecast:
    first, last = window
    horizon = [last + timedelta(weeks=h) for h in range(1, cfg.horizon + 1)]
    elig = account_eligibility(tables["b2b_accounts"], horizon[0])
    ratios = ratio_series(demand, elig, first, last)
    dist = ratio_distribution(ratios)
    if ratios.is_empty():
        return B2BForecast(
            elig, ratios, dist, None,
            pl.DataFrame(schema={"series_id": pl.String, "account_id": pl.String,
                                 "week_start": pl.Date, "h": pl.Int32, "q10": pl.Float64,
                                 "q50": pl.Float64, "q90": pl.Float64}),
            [], np.zeros((0, cfg.n_paths, cfg.horizon)),
        )  # fmt: skip

    sku = tables["skus"].filter(pl.col("status") == "production")["sku_id"].first()
    meta = elig.filter(pl.col("included")).select(
        pl.col("account_id").map_elements(b2b_series_id, return_dtype=pl.String).alias("series_id"),
        pl.lit("B2B").alias("channel"),
        "region_id",
        pl.lit(sku).alias("sku_id"),
        "account_id",
    )
    cov = build_covariates(tables, meta, first, horizon[-1])
    groups = cov.group_by("series_id").agg(pl.col("channel", "tier").first())
    series = ratios.select(
        "series_id", "week_start", pl.col("ratio").alias("y"), pl.lit("B2B").alias("channel"),
        "is_censored",
    )  # fmt: skip
    fs = run_forecast(series, groups, cov, cfg, seed)

    commit = elig.filter(pl.col("included")).select(
        pl.col("account_id").map_elements(b2b_series_id, return_dtype=pl.String).alias("series_id"),
        "account_id",
        "weekly_commit_kg",
        "contract_end",
    )
    live = pl.col("contract_end").is_null() | (pl.col("week_start") <= pl.col("contract_end"))
    kg = (
        fs.quantiles.join(commit, on="series_id")
        .with_columns(
            [
                pl.when(live).then(pl.col(q) * pl.col("weekly_commit_kg")).otherwise(0.0).alias(q)
                for q in ("q10", "q50", "q90")
            ]
        )
        .select("series_id", "account_id", "week_start", "h", "q10", "q50", "q90")
        .sort("series_id", "week_start")
    )
    c = {r["series_id"]: r for r in commit.iter_rows(named=True)}
    mult = np.zeros((len(fs.path_series), len(horizon)))
    for i, sid in enumerate(fs.path_series):
        end = c[sid]["contract_end"]
        for h, wk in enumerate(horizon):
            mult[i, h] = c[sid]["weekly_commit_kg"] if end is None or wk <= end else 0.0
    return B2BForecast(
        accounts=elig,
        ratio_history=ratios,
        ratio_distribution=dist,
        ratio_forecast=fs,
        quantiles_kg=kg,
        path_series=fs.path_series,
        paths_kg=fs.paths * mult[:, None, :],
    )

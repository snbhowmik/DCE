"""Dataset-level forecast: D2C per region × SKU + B2B per account (ARCH §5.4).

Takes no strategy mode (IDEATION P1); T2.8 asserts hash equality across modes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from dce.demand.reconstruct import weekly_demand
from dce.forecast.b2b import B2BForecast, forecast_b2b
from dce.forecast.covariates import build_covariates, series_meta
from dce.forecast.paths import paths_frame
from dce.forecast.pipeline import ForecastConfig, ForecastSet, run_forecast
from dce.numerics import SUM_DECIMALS
from dce.scope import planning_skus


@dataclass
class DatasetForecast:
    horizon: list[date]
    d2c: ForecastSet
    b2b: B2BForecast
    series: pl.DataFrame  # series_id, channel, region_id, account_id, sku_id
    out_of_scope: pl.DataFrame  # demand for SKUs outside the planned line (A-001), by SKU

    def quantiles(self) -> pl.DataFrame:
        """Demand quantiles in kg for every forecast series."""
        d2c = self.d2c.quantiles.select("series_id", "week_start", "h", "q10", "q50", "q90")
        b2b = self.b2b.quantiles_kg.select("series_id", "week_start", "h", "q10", "q50", "q90")
        return (
            pl.concat([d2c, b2b], how="vertical_relaxed")
            .join(self.series, on="series_id", how="left")
            .sort("series_id", "week_start")
        )

    def paths(self) -> tuple[list[str], np.ndarray]:
        """(series order, kg paths [S, n_paths, H]) over D2C then B2B series."""
        parts = [p for p in (self.d2c.paths, self.b2b.paths_kg) if p.size]
        arr = np.concatenate(parts, axis=0) if parts else np.zeros((0, 0, len(self.horizon)))
        return [*self.d2c.path_series, *self.b2b.path_series], arr

    def artifact_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.d2c.artifact_hash().encode())
        if self.b2b.ratio_forecast is not None:
            h.update(self.b2b.ratio_forecast.artifact_hash().encode())
        sids, arr = self.paths()
        h.update("|".join(sids).encode())
        h.update(np.ascontiguousarray(np.round(arr, 10)).tobytes())
        return h.hexdigest()

    def write(self, out: Path) -> dict[str, Path]:
        files = self.d2c.write(out / "d2c")
        if self.b2b.ratio_forecast is not None:
            files |= {f"b2b_{k}": v for k, v in self.b2b.ratio_forecast.write(out / "b2b").items()}
        files["demand_quantiles"] = out / "demand_quantiles.parquet"
        self.quantiles().write_parquet(files["demand_quantiles"])
        sids, arr = self.paths()
        files["demand_paths"] = out / "demand_paths.parquet"
        paths_frame(sids, self.horizon, arr).write_parquet(files["demand_paths"])
        files["b2b_accounts"] = out / "b2b_accounts.parquet"
        self.b2b.accounts.write_parquet(files["b2b_accounts"])
        files["b2b_ratio_distribution"] = out / "b2b_ratio_distribution.parquet"
        self.b2b.ratio_distribution.write_parquet(files["b2b_ratio_distribution"])
        return files


def forecast_dataset(
    tables: dict[str, pl.DataFrame],
    window: tuple[date, date],
    app_cfg: dict[str, Any],
    scoring_cfg: dict[str, Any],
    seed: int,
) -> DatasetForecast:
    first, last = window
    cfg = ForecastConfig.from_config(app_cfg, scoring_cfg)
    horizon = [last + timedelta(weeks=h) for h in range(1, cfg.horizon + 1)]
    all_demand = weekly_demand(tables["orders"], last)
    in_scope = pl.col("sku_id").is_in(planning_skus(tables["skus"]))
    demand = all_demand.filter(in_scope)
    out_of_scope = (
        all_demand.filter(~in_scope)
        .group_by("channel", "sku_id")
        .agg(
            pl.col("demand_kg").sum().round(SUM_DECIMALS),
            pl.col("week_start").min().alias("first_week"),
        )
        .sort("channel", "sku_id")
    )

    d2c_demand = demand.filter(pl.col("channel") == "D2C")
    meta = series_meta(d2c_demand)
    cov = build_covariates(tables, meta, first, horizon[-1])
    groups = cov.group_by("series_id").agg(pl.col("channel", "tier").first())
    series = d2c_demand.select(
        "series_id", "week_start", pl.col("demand_kg").alias("y"), "channel", "is_censored"
    )
    d2c = run_forecast(series, groups, cov, cfg, seed)
    b2b = forecast_b2b(tables, demand, window, cfg, seed)

    b2b_meta = b2b.accounts.filter(pl.col("included")).select(
        pl.concat_str([pl.lit("B2B"), pl.col("account_id")], separator="|").alias("series_id"),
        pl.lit("B2B").alias("channel"),
        "region_id",
        "account_id",
        pl.lit(None, dtype=pl.String).alias("sku_id"),
    )
    series_all = pl.concat([meta.select(b2b_meta.columns), b2b_meta], how="vertical_relaxed")
    return DatasetForecast(
        horizon=horizon, d2c=d2c, b2b=b2b, series=series_all, out_of_scope=out_of_scope
    )

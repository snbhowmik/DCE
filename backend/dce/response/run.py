"""Fit the spend response for every region from a loaded dataset (ARCH §5.6)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from dce.demand.reconstruct import weekly_demand
from dce.forecast.anomaly import AnomalyConfig, detect_anomalies
from dce.hashing import canonical_json
from dce.numerics import SUM_DECIMALS
from dce.response.fit import ResponseConfig, ResponseFit, fit_region


@dataclass
class ResponseSet:
    fits: dict[str, ResponseFit]
    skipped: dict[str, str]  # region → reason (e.g. no D2C demand or no spend)

    def summary(self) -> pl.DataFrame:
        rows = [f.summary() for f in self.fits.values()]
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    def artifact_hash(self) -> str:
        payload = {
            "fits": {r: f.summary() for r, f in sorted(self.fits.items())},
            "skipped": self.skipped,
        }
        return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def weekly_region_inputs(
    tables: dict[str, pl.DataFrame], window: tuple[date, date], scoring: dict[str, Any]
) -> pl.DataFrame:
    """`region_id, week_start, demand (anomaly-cleaned D2C), spend (realized D2C)`."""
    first, last = window
    d = (
        weekly_demand(tables["orders"], last)
        .filter(pl.col("channel") == "D2C")
        .group_by("region_id", "week_start")
        .agg(pl.col("demand_kg").sum().round(SUM_DECIMALS).alias("y"), pl.col("is_censored").any())
        .with_columns(
            pl.concat_str([pl.lit("D2C"), pl.col("region_id")], separator="|").alias("series_id"),
            pl.lit("D2C").alias("channel"),
        )
    )
    clean = detect_anomalies(d, AnomalyConfig.from_scoring(scoring)).select(
        "region_id", "week_start", pl.col("y_clean").alias("demand")
    )
    spend = (
        tables["marketing_daily"]
        .filter(pl.col("channel") == "D2C")
        .with_columns(pl.col("date").dt.truncate("1w").alias("week_start"))
        .group_by("region_id", "week_start")
        .agg(pl.col("spend_inr").sum().round(SUM_DECIMALS).alias("spend"))
    )
    return (
        clean.filter(pl.col("week_start").is_between(first, last))
        .join(spend, on=["region_id", "week_start"], how="left")
        .with_columns(pl.col("spend").fill_null(0.0))
        .sort("region_id", "week_start")
    )


def fit_responses(
    tables: dict[str, pl.DataFrame],
    window: tuple[date, date],
    app: dict[str, Any],
    scoring: dict[str, Any],
    seed: int,
) -> ResponseSet:
    cfg = ResponseConfig.from_app(app)
    data = weekly_region_inputs(tables, window, scoring)
    fits: dict[str, ResponseFit] = {}
    skipped: dict[str, str] = {}
    for region in tables["regions"]["region_id"].sort().to_list():
        g = data.filter(pl.col("region_id") == region)
        if g.height < 52:
            skipped[region] = f"only {g.height} weeks of D2C demand"
            continue
        if float(g["spend"].sum()) <= 0:
            skipped[region] = "no D2C spend history"
            continue
        woy = np.array([w.isocalendar()[1] for w in g["week_start"].to_list()], dtype=float)
        fits[region] = fit_region(
            region, g["demand"].to_numpy(), g["spend"].to_numpy(), cfg, seed, week_of_year=woy
        )
    return ResponseSet(fits=fits, skipped=skipped)

"""Anomaly detection and winsorization of weekly demand (ARCH §5.4, PRD FR-9).

Residual r_t = y_t − centered rolling median(y). Robust z = 0.6745·(r − median r)/MAD(r) per
series (falls back to mean absolute deviation when MAD = 0; no flags if both are 0). Points with
|z| > threshold are flagged; for training they are clipped to baseline ± threshold·σ̂ so a spike
is never extrapolated. Negative flags in censored (stockout/waitlist) weeks are not anomalies:
censoring already explains them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

K = 0.6745  # MAD → σ for a normal distribution


@dataclass(frozen=True)
class AnomalyConfig:
    z_threshold: float = 3.5
    window_weeks: int = 9
    channels: tuple[str, ...] = field(default=("D2C",))

    @classmethod
    def from_scoring(cls, scoring: dict[str, Any]) -> AnomalyConfig:
        a = scoring.get("anomaly", {})
        d = cls()
        return cls(
            z_threshold=float(a.get("z_threshold", d.z_threshold)),
            window_weeks=int(a.get("window_weeks", d.window_weeks)),
            channels=tuple(a.get("channels", d.channels)),
        )


def detect_anomalies(demand: pl.DataFrame, cfg: AnomalyConfig | None = None) -> pl.DataFrame:
    """Add `baseline, robust_z, is_anomaly, y_clean` to a demand frame with `y`.

    Requires `series_id, week_start, channel, y`; uses `is_censored` if present.
    """
    cfg = cfg or AnomalyConfig()
    censored = pl.col("is_censored") if "is_censored" in demand.columns else pl.lit(False)
    d = demand.sort("series_id", "week_start").with_columns(
        pl.col("y")
        .rolling_median(cfg.window_weeks, center=True, min_samples=1)
        .over("series_id")
        .alias("baseline")
    )
    d = d.with_columns((pl.col("y") - pl.col("baseline")).alias("_r"))
    med = pl.col("_r").median().over("series_id")
    mad = (pl.col("_r") - med).abs().median().over("series_id")
    mean_ad = (pl.col("_r") - med).abs().mean().over("series_id")
    # σ̂ from MAD; fall back to mean absolute deviation (×1.2533 → σ) if MAD is 0.
    sigma = pl.when(mad > 0).then(mad / K).when(mean_ad > 0).then(mean_ad * 1.2533).otherwise(None)
    d = d.with_columns(sigma.alias("_sigma"), med.alias("_med"))
    z = ((pl.col("_r") - pl.col("_med")) / pl.col("_sigma")).fill_null(0.0)
    eligible = pl.col("channel").is_in(list(cfg.channels))
    flagged = eligible & (z.abs() > cfg.z_threshold) & ~((z < 0) & censored)
    cap = cfg.z_threshold * pl.col("_sigma")
    clipped = (
        pl.col("baseline") + pl.col("_med") + (pl.col("_r") - pl.col("_med")).clip(-cap, cap)
    ).clip(lower_bound=0.0)
    return d.with_columns(
        z.alias("robust_z"),
        flagged.alias("is_anomaly"),
        pl.when(flagged).then(clipped).otherwise(pl.col("y")).alias("y_clean"),
    ).drop("_r", "_sigma", "_med")


def anomaly_report(flagged: pl.DataFrame) -> pl.DataFrame:
    """Flagged points for the UI / health report."""
    return flagged.filter(pl.col("is_anomaly")).select(
        "series_id", "week_start", "y", "baseline", "robust_z", "y_clean"
    )

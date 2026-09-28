"""Run orchestration. Stages receive the full run config; each stage takes only what it may see.

The forecast stage deliberately reads only `app` and `scoring` sections and the seed, never the
`mode` section (IDEATION P1, T2.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl

from dce.capacity.model import CapacityForecast, capacity_forecast
from dce.config import load_app_config, load_scoring_config
from dce.forecast.run import DatasetForecast, forecast_dataset
from dce.strategy import load_strategy_modes


@dataclass(frozen=True)
class RunConfig:
    mode: str
    mode_config: dict[str, Any]
    app: dict[str, Any]
    scoring: dict[str, Any]
    seed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mode_config": self.mode_config,
            "app": self.app,
            "scoring": self.scoring,
            "seed": self.seed,
        }


def build_run_config(mode: str, seed: int | None = None, **overrides: Any) -> RunConfig:
    modes = load_strategy_modes()
    if mode not in modes:
        raise KeyError(f"unknown mode {mode!r}; expected one of {sorted(modes)}")
    app = load_app_config() | overrides.get("app", {})
    return RunConfig(
        mode=mode,
        mode_config=modes[mode] | overrides.get("mode_config", {}),
        app=app,
        scoring=load_scoring_config() | overrides.get("scoring", {}),
        seed=int(seed if seed is not None else app["seed"]),
    )


def forecast_stage(
    tables: dict[str, pl.DataFrame], window: tuple[date, date], run: RunConfig
) -> DatasetForecast:
    return forecast_dataset(tables, window, run.app, run.scoring, run.seed)


def capacity_stage(
    tables: dict[str, pl.DataFrame], window: tuple[date, date], run: RunConfig
) -> CapacityForecast:
    return capacity_forecast(tables, window, run.app, run.seed)

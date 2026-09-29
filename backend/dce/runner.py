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
from dce.metrics.scores import EvidenceScores, evidence_scores
from dce.optimize.coman import CoManInputs, already_active_partners
from dce.optimize.inputs import ModeParams, PlanInputs, build_inputs
from dce.optimize.lp import PlanResult
from dce.optimize.plan import build_spend_inputs, solve_plan
from dce.optimize.spend import SpendInputs
from dce.response.run import ResponseSet, fit_responses
from dce.strategy import resolve_mode


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


def build_run_config(
    mode: str,
    seed: int | None = None,
    mode_overrides: dict[str, Any] | None = None,
    **overrides: Any,
) -> RunConfig:
    """Validated run config. `mode_overrides` are CUSTOM-mode levers; `overrides` patch app,
    scoring or mode_config sections (tests / scenarios)."""
    mc = resolve_mode(mode, mode_overrides).model_dump()
    app = load_app_config() | overrides.get("app", {})
    return RunConfig(
        mode=mode,
        mode_config=mc | overrides.get("mode_config", {}),
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


def response_stage(
    tables: dict[str, pl.DataFrame], window: tuple[date, date], run: RunConfig
) -> ResponseSet:
    return fit_responses(tables, window, run.app, run.scoring, run.seed)


@dataclass
class PipelineOutputs:
    """Everything one run produces; the mode-independent stages can be reused across modes."""

    run: RunConfig
    window: tuple[date, date]
    forecast: DatasetForecast
    capacity: CapacityForecast
    responses: ResponseSet
    scores: EvidenceScores
    mode: ModeParams
    inputs: PlanInputs
    spend: SpendInputs
    coman: CoManInputs
    plan: PlanResult


@dataclass
class UpstreamOutputs:
    """Mode-independent stages (P1): computed once, shared by every mode's plan."""

    forecast: DatasetForecast
    capacity: CapacityForecast
    responses: ResponseSet


def upstream_stage(
    tables: dict[str, pl.DataFrame], window: tuple[date, date], run: RunConfig
) -> UpstreamOutputs:
    return UpstreamOutputs(
        forecast=forecast_stage(tables, window, run),
        capacity=capacity_stage(tables, window, run),
        responses=response_stage(tables, window, run),
    )


def plan_stage(
    tables: dict[str, pl.DataFrame],
    window: tuple[date, date],
    run: RunConfig,
    up: UpstreamOutputs,
) -> PipelineOutputs:
    opt = run.app.get("optimize", {})
    mode = ModeParams.from_mode_config(run.mode, run.mode_config, opt)
    profile = run.mode_config.get("onboarding_aqs_weights") or "balanced"
    scores = evidence_scores(tables, window, run.scoring, profile)
    inputs = build_inputs(tables, up.forecast, up.capacity, mode, opt)
    spend = build_spend_inputs(tables, inputs, up.responses, scores, run.app)
    coman = CoManInputs(
        partners=up.capacity.partners,
        decision_week=up.forecast.horizon[0],
        already_active=already_active_partners(tables["coman_activity"], window[1]),
    )
    plan = solve_plan(
        inputs,
        mode,
        spend,
        coman,
        time_limit=float(opt.get("time_limit_s", 10)),
        mip_gap=float(opt.get("mip_gap", 0.005)),
    )
    return PipelineOutputs(
        run, window, up.forecast, up.capacity, up.responses, scores, mode, inputs, spend, coman,
        plan,
    )  # fmt: skip


def run_pipeline(
    tables: dict[str, pl.DataFrame], window: tuple[date, date], run: RunConfig
) -> PipelineOutputs:
    return plan_stage(tables, window, run, upstream_stage(tables, window, run))

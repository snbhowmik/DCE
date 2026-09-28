"""Strategy modes (IDEATION §8). The ONLY module that reads `config/strategy_modes.yaml`.

Allowed consumers: optimize, risk, mitigate, onboarding (and the run orchestrator). Upstream
modules (forecast, capacity, response, metrics, demand) are forbidden to import this package
(`.importlinter`), which is how P1 is enforced structurally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dce import paths
from dce.config import load_scoring_config, load_yaml
from dce.strategy.models import ModeConfig, custom_mode, validate_modes

MODES_FILE = "strategy_modes.yaml"


def load_strategy_modes(config_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    return load_yaml((config_dir or paths.CONFIG_DIR) / MODES_FILE)


def load_modes(config_dir: Path | None = None) -> dict[str, ModeConfig]:
    """Validated modes; AQS profile names are cross-checked against scoring.yaml."""
    raw = load_strategy_modes(config_dir)
    profiles = set(load_scoring_config(config_dir).get("aqs", {}).get("profiles", {}))
    return validate_modes(raw, profiles or None)


def resolve_mode(
    name: str, overrides: dict[str, Any] | None = None, config_dir: Path | None = None
) -> ModeConfig:
    """A named mode, or CUSTOM with user overrides applied and validated."""
    modes = load_modes(config_dir)
    if name not in modes:
        raise KeyError(f"unknown mode {name!r}; expected one of {sorted(modes)}")
    if overrides:
        if name != "CUSTOM":
            raise ValueError("overrides are only allowed for the CUSTOM mode")
        return custom_mode(modes[name], overrides)
    return modes[name]


__all__ = ["ModeConfig", "load_modes", "load_strategy_modes", "resolve_mode"]

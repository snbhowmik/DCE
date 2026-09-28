"""Strategy modes (IDEATION §8). The ONLY module that reads `config/strategy_modes.yaml`.

Allowed consumers: optimize, risk, mitigate, onboarding (and the run orchestrator). Upstream
modules (forecast, capacity, response, metrics, demand) are forbidden to import this package
(`.importlinter`), which is how P1 is enforced structurally. Pydantic validation lands in T5.2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dce import paths
from dce.config import load_yaml

MODES_FILE = "strategy_modes.yaml"


def load_strategy_modes(config_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    return load_yaml((config_dir or paths.CONFIG_DIR) / MODES_FILE)

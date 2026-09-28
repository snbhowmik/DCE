"""Configuration loading. Strategy-mode validation lands in T5.2."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dce import paths


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping")
    return data


def load_app_config(config_dir: Path | None = None) -> dict[str, Any]:
    return load_yaml((config_dir or paths.CONFIG_DIR) / "app.yaml")

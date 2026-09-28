"""T2.8 / ARCH §9.4: the forecast is byte-identical under every strategy mode (IDEATION P1)."""

from __future__ import annotations

import re
import subprocess
from datetime import date

import polars as pl
import pytest

from dce import paths
from dce.runner import build_run_config, forecast_stage
from dce.strategy import load_strategy_modes

UPSTREAM = ("forecast", "capacity", "response", "metrics", "demand")
SMALL = {
    "app": {
        "n_paths": 40,
        "forecast": {"models": ["seasonal_naive_52", "window_average_8", "lightgbm"]},
    }
}


def test_all_modes_defined() -> None:
    assert {"GROWTH", "STABILITY", "D2C_EXPANSION", "CUSTOM"} <= set(load_strategy_modes())


def test_forecast_hash_identical_across_modes(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    hashes = {}
    for mode in load_strategy_modes():
        run = build_run_config(mode, seed=17, **SMALL)
        assert run.mode_config  # the stage *receives* mode config …
        hashes[mode] = forecast_stage(tiny_tables, tiny_window, run).artifact_hash()
    assert len(set(hashes.values())) == 1, hashes  # … and ignores it


def test_hash_is_sensitive(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    """Guard against a vacuous invariance test: the hash does change when inputs change."""
    a = forecast_stage(tiny_tables, tiny_window, build_run_config("GROWTH", seed=17, **SMALL))
    b = forecast_stage(tiny_tables, tiny_window, build_run_config("GROWTH", seed=18, **SMALL))
    assert a.artifact_hash() != b.artifact_hash()


@pytest.mark.parametrize("module", UPSTREAM)
def test_upstream_source_never_mentions_strategy(module: str) -> None:
    """A direct YAML read would bypass the import graph; forbid it textually too."""
    pattern = re.compile(r"strategy_modes|dce\.strategy|from dce import strategy")
    for py in (paths.ROOT / "backend" / "dce" / module).rglob("*.py"):
        assert not pattern.search(py.read_text()), py


def test_import_linter_contract_holds() -> None:
    res = subprocess.run(
        ["lint-imports", "--config", str(paths.ROOT / ".importlinter")],
        cwd=paths.ROOT / "backend",
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "KEPT" in res.stdout

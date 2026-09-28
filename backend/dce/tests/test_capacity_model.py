"""T3.3: capacity quantiles/paths, perishability, uncertainty from failure history."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from dce.capacity.model import CapacityForecast, capacity_forecast, carryover_weeks

APP = {"horizon_weeks": 13, "n_paths": 2000, "capacity": {"carryover_cap_weeks": 1}}


@pytest.mark.parametrize(
    ("shelf", "cap", "expected"),
    [(3, 1, 0), (6, 4, 0), (7, 1, 1), (10, 1, 1), (21, 1, 1), (21, 4, 3), (60, 4, 4)],
)
def test_carryover_rule(shelf: int, cap: int, expected: int) -> None:
    assert carryover_weeks(shelf, cap) == expected


def _with_failures(tables: dict[str, pl.DataFrame], every: int) -> dict[str, pl.DataFrame]:
    t = dict(tables)
    t["capacity_batches"] = tables["capacity_batches"].with_columns(
        pl.when(pl.int_range(pl.len()) % every == 0)
        .then(pl.lit("failed"))
        .otherwise(pl.col("outcome"))
        .alias("outcome"),
        pl.when(pl.int_range(pl.len()) % every == 0)
        .then(0.0)
        .otherwise(pl.col("actual_yield_kg"))
        .alias("actual_yield_kg"),
    )
    t["capacity_plan"] = tables["capacity_plan"].with_columns(pl.lit(3).alias("planned_batches"))
    return t


def test_failure_heavy_history_widens_band(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    calm = capacity_forecast(_with_failures(tiny_tables, 50), tiny_window, APP, seed=1)
    rough = capacity_forecast(_with_failures(tiny_tables, 3), tiny_window, APP, seed=1)

    def width(f: CapacityForecast) -> float:
        return float((f.quantiles["q90"] - f.quantiles["q10"]).to_numpy().mean())

    assert width(rough) > 1.5 * width(calm)
    assert rough.quantiles["q50"].to_numpy().mean() < calm.quantiles["q50"].to_numpy().mean()


def test_quantiles_paths_and_accessor(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    f = capacity_forecast(tiny_tables, tiny_window, APP, seed=2)
    assert f.product_line == "cultivated_chicken"
    assert f.paths.shape == (2000, 13)
    assert f.horizon[0] == tiny_window[1] + timedelta(weeks=1)
    q = f.quantiles
    assert (q["q10"] <= q["q50"]).all() and (q["q50"] <= q["q90"]).all()
    assert np.allclose(f.at_quantile(0.5), q["q50"].to_numpy())
    assert (f.at_quantile(0.15) <= f.at_quantile(0.5)).all()  # STABILITY plans lower
    assert f.perishability.shelf_life_days == 10 and f.perishability.carryover_weeks == 1
    assert f.perishability.waste_cost_inr_per_kg == 900
    assert [p.coman_id for p in f.partners] == ["CM_A", "CM_B"]
    assert (
        f.artifact_hash()
        == capacity_forecast(tiny_tables, tiny_window, APP, seed=2).artifact_hash()
    )


def test_capacity_mode_invariant(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date]
) -> None:
    from dce.runner import build_run_config, capacity_stage
    from dce.strategy import load_strategy_modes

    hashes = {
        m: capacity_stage(tiny_tables, tiny_window, build_run_config(m, seed=9)).artifact_hash()
        for m in load_strategy_modes()
    }
    assert len(set(hashes.values())) == 1


def test_writes_artifacts(
    tiny_tables: dict[str, pl.DataFrame], tiny_window: tuple[date, date], tmp_path: Path
) -> None:
    files = capacity_forecast(tiny_tables, tiny_window, APP | {"n_paths": 20}, seed=0).write(
        tmp_path
    )
    assert all(p.is_file() for p in files.values())

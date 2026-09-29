"""Run payload + persisted runs (D-051): the contract between pipeline, API, UI and AI layer."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dce import paths
from dce.payload import clean
from dce.store import db

FAST = {
    "n_paths": 60,
    "forecast": {
        "quantiles": [0.1, 0.5, 0.9],
        "backtest": {"n_folds": 3, "step_weeks": 4, "min_train_weeks": 52},
        "mase_seasonality": 52,
        "n_jobs": 1,
        "b2b_rolling_renewal": True,
        "models": ["seasonal_naive_52", "window_average_8"],
    },
}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Any, list[Any]]]:
    """tiny_world ingested into a sandbox DB and planned in two modes (fixture: code paths only)."""
    from dce.ingest import ingest
    from dce.service import run_modes

    tmp = tmp_path_factory.mktemp("svc")
    mp = pytest.MonkeyPatch()
    mp.setattr(paths, "PROCESSED_DIR", tmp / "processed")
    mp.setattr(db, "default_db_path", lambda: tmp / "dce.sqlite")
    engine = db.make_engine()
    assert ingest(paths.FIXTURES_DIR / "tiny_world", engine=engine).ok
    base = __import__("dce.config", fromlist=["x"]).load_app_config()
    recs = run_modes(
        engine,
        "tiny_world",
        ["STABILITY", "GROWTH"],
        seed=5,
        app_overrides={**FAST, "forecast": base["forecast"] | FAST["forecast"]},
    )
    yield engine, recs
    mp.undo()


def _load(rec: Any) -> dict[str, Any]:
    return json.loads(Path(rec.payload_path).read_text())


def test_runs_succeed_and_are_listed(runs: tuple[Any, list[Any]]) -> None:
    from dce.service import latest_runs, payload_path

    engine, recs = runs
    assert [r.status for r in recs] == ["succeeded", "succeeded"]
    latest = {(w, r.mode): r.run_id for r, w in latest_runs(engine)}
    assert latest == {("tiny_world", r.mode): r.run_id for r in recs}
    assert payload_path(engine, recs[0].run_id) == recs[0].payload_path


def test_payload_is_strict_json_with_provenance(runs: tuple[Any, list[Any]]) -> None:
    _, recs = runs
    p = _load(recs[0])
    json.dumps(p, allow_nan=False)  # no NaN / inf anywhere
    run = p["run"]
    assert run["run_id"] == recs[0].run_id and run["mode"] == "STABILITY"
    assert run["dataset_hash"] and run["config_hash"] and run["world_id"] == "tiny_world"
    assert set(p) >= {"kpis", "demand", "capacity", "plan", "stress", "risk", "markets", "health"}


def test_payload_numbers_are_consistent(runs: tuple[Any, list[Any]]) -> None:
    p = _load(runs[1][0])
    H = len(p["run"]["horizon"])
    alloc = p["plan"]["allocation"]
    assert p["kpis"]["allocated_kg"] == pytest.approx(sum(r["allocated_kg"] for r in alloc), 0.01)
    fc = p["demand"]["forecast"]["total"]
    assert all(len(fc[q]) == H for q in ("q10", "q50", "q90"))
    assert all(a <= b <= c for a, b, c in zip(fc["q10"], fc["q50"], fc["q90"], strict=True))
    assert len(p["risk"]["weekly"]) == H
    assert all(a["start"] in p["run"]["horizon"] for a in p["risk"]["alerts"])
    assert {m["region_id"] for m in p["markets"]} == set(p["run"]["regions"])
    assert p["risk"]["breach_threshold"] == p["run"]["mode_config"]["breach_threshold"]


def test_forecast_hash_identical_across_modes(runs: tuple[Any, list[Any]]) -> None:
    """P1 through the persisted payloads: modes share one forecast."""
    a, b = (_load(r) for r in runs[1])
    assert a["run"]["forecast_hash"] == b["run"]["forecast_hash"]
    assert a["demand"]["forecast"] == b["demand"]["forecast"]


def test_clean() -> None:
    assert clean(-0.0) == 0.0 and str(clean(-0.0)) == "0.0"
    assert clean(float("nan")) is None and clean(np.inf) is None
    assert clean(date(2025, 1, 6)) == "2025-01-06"
    assert clean({"a": np.int64(3), "b": np.bool_(True), "c": np.array([1.23456, 2.0])}) == {
        "a": 3,
        "b": True,
        "c": [1.2346, 2.0],
    }
    assert clean(1234567.891) == 1234567.9

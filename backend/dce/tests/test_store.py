"""T0.4: runs record provenance; identical inputs give identical hashes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from dce import paths
from dce.contract.spec import TABLES
from dce.hashing import config_hash, dataset_hash
from dce.seeds import derive_seed, rng
from dce.store.db import make_engine
from dce.store.models import Run, RunArtifact
from dce.store.runs import add_artifact, finish_run, new_run_id, start_run

TINY = paths.FIXTURES_DIR / "tiny_world"
CONTRACT_FILES = [t.filename for t in TABLES] + ["manifest.json"]


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return make_engine(tmp_path / "t.sqlite")


def _dummy_run(engine: Engine) -> Run:
    cfg = {"mode": "STABILITY", "q_capacity": 0.15, "weights": {"rev": 1.0, "pen": 3.0}}
    run = start_run(
        engine,
        dataset_hash=dataset_hash(TINY, CONTRACT_FILES),
        config=cfg,
        mode="STABILITY",
        seed=42,
        kind="dummy",
    )
    return finish_run(engine, run.run_id)


def test_dummy_run_writes_row_with_all_hashes(engine: Engine) -> None:
    run = _dummy_run(engine)
    with Session(engine) as s:
        row = s.exec(select(Run).where(Run.run_id == run.run_id)).one()
    assert re.fullmatch(r"[0-9a-f]{64}", row.dataset_hash)
    assert re.fullmatch(r"[0-9a-f]{64}", row.config_hash)
    assert row.git_sha and row.git_sha != ""
    assert row.status == "succeeded" and row.finished_at is not None
    assert row.config["weights"]["pen"] == 3.0


def test_same_inputs_same_hashes(engine: Engine) -> None:
    a, b = _dummy_run(engine), _dummy_run(engine)
    assert a.run_id != b.run_id
    assert (a.dataset_hash, a.config_hash, a.git_sha) == (b.dataset_hash, b.config_hash, b.git_sha)


def test_config_hash_ignores_key_order_but_not_values() -> None:
    assert config_hash({"a": 1, "b": {"c": 2, "d": 3}}) == config_hash(
        {"b": {"d": 3, "c": 2}, "a": 1}
    )
    assert config_hash({"a": 1}) != config_hash({"a": 1.5})


def test_dataset_hash_tracks_content_not_extras(tmp_path: Path) -> None:
    (tmp_path / "regions.csv").write_text("region_id\nR1\n")
    h1 = dataset_hash(tmp_path, CONTRACT_FILES)
    (tmp_path / "README.md").write_text("not part of the contract")
    assert dataset_hash(tmp_path, CONTRACT_FILES) == h1
    (tmp_path / "regions.csv").write_text("region_id\nR2\n")
    assert dataset_hash(tmp_path, CONTRACT_FILES) != h1


def test_seed_derivation_is_stable_and_distinct() -> None:
    assert derive_seed(1, "forecast", "R_N") == derive_seed(1, "forecast", "R_N")
    assert derive_seed(1, "forecast", "R_N") != derive_seed(1, "forecast", "R_S")
    assert derive_seed(1, "forecast", "R_N") != derive_seed(2, "forecast", "R_N")
    assert 0 <= derive_seed(123, "x") < 2**63
    assert rng(5, "cap").random() == rng(5, "cap").random()


def test_run_id_format() -> None:
    assert re.fullmatch(r"run_\d{8}T\d{6}_[0-9a-f]{8}", new_run_id())


def test_artifact_recorded_with_hash(engine: Engine, tmp_path: Path) -> None:
    run = _dummy_run(engine)
    p = tmp_path / "a.txt"
    p.write_text("x")
    add_artifact(engine, run.run_id, "narrative", p)
    with Session(engine) as s:
        art = s.exec(select(RunArtifact)).one()
    assert art.sha256 is not None and len(art.sha256) == 64

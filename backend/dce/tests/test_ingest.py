"""T1.1: ingest writes report + Parquet; the path guard only admits allowed data roots."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from dce import paths
from dce.cli import app
from dce.ingest import PathNotAllowed, guard_path, ingest, load_dataset
from dce.store import db
from dce.store.models import Dataset

TINY = paths.FIXTURES_DIR / "tiny_world"


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point processed output + DB at tmp; keep the real fixtures dir as an allowed root."""
    monkeypatch.setattr(paths, "PROCESSED_DIR", tmp_path / "processed")
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "dce.sqlite")
    return tmp_path


def test_cli_ingest_tiny_world(sandbox: Path) -> None:
    result = CliRunner().invoke(app, ["ingest", str(TINY)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    [out] = list((sandbox / "processed").iterdir())
    report = json.loads((out / "validation_report.json").read_text())
    assert report["ok"] and report["world_id"] == "tiny_world"
    assert report["history"]["n_weeks"] == "110"
    assert (out / "validation_report.md").is_file()
    assert (out / "orders.parquet").is_file()
    tables = load_dataset(out.name)
    assert tables["orders"].height == report["tables"]["orders"]["rows"]
    with Session(db.make_engine()) as s:
        row = s.exec(select(Dataset)).one()
    assert row.dataset_hash == out.name and row.n_errors == 0


def test_ingest_is_idempotent(sandbox: Path) -> None:
    a, b = ingest(TINY), ingest(TINY)
    assert a.dataset_hash == b.dataset_hash


@pytest.mark.parametrize(
    "bad",
    [
        "data/fixtures/../../docs",
        "../dgp",
        "/etc",
        "/tmp",
        "data/incoming/../../../",
    ],
)
def test_guard_rejects_outside_paths(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(paths.ROOT)
    with pytest.raises(PathNotAllowed):
        guard_path(bad)


def test_guard_rejects_root_dirs_themselves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(paths.ROOT)
    for bad in ("data/fixtures", "data/incoming", "data/fixtures/tiny_world/.."):
        with pytest.raises(PathNotAllowed):
            guard_path(bad)


def test_guard_rejects_symlink_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (incoming / "world_x").symlink_to(outside)
    monkeypatch.setattr(paths, "INCOMING_DIR", incoming)
    with pytest.raises(PathNotAllowed):
        guard_path(incoming / "world_x")


def test_guard_accepts_relative_and_absolute_inside(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(paths.ROOT)
    assert guard_path("data/fixtures/tiny_world") == TINY.resolve()
    assert guard_path(TINY.resolve()) == TINY.resolve()


def test_cli_guard_exit_code(sandbox: Path) -> None:
    result = CliRunner().invoke(app, ["ingest", "/etc"])
    assert result.exit_code == 2


def test_invalid_world_writes_report_but_no_parquet(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = sandbox / "incoming"
    world = incoming / "world_bad"
    shutil.copytree(TINY, world)
    plan = (world / "capacity_plan.csv").read_text().splitlines()
    (world / "capacity_plan.csv").write_text("\n".join(plan[:50]) + "\n")  # cut forward coverage
    monkeypatch.setattr(paths, "INCOMING_DIR", incoming)
    report = ingest(world)
    assert not report.ok
    assert any(i.rule == "plan_coverage" and i.severity == "error" for i in report.issues)
    out = sandbox / "processed" / report.dataset_hash
    assert (out / "validation_report.json").is_file()
    assert not (out / "orders.parquet").exists()


def test_short_history_is_an_error(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = sandbox / "incoming"
    world = incoming / "world_short"
    shutil.copytree(TINY, world)
    manifest = json.loads((world / "manifest.json").read_text())
    manifest["history_start"] = "2025-06-02"
    (world / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(paths, "INCOMING_DIR", incoming)
    report = ingest(world)
    rules = {i.rule for i in report.issues if i.severity == "error"}
    assert "min_history" in rules
    assert any(i.rule == "out_of_range" for i in report.issues)


def test_spend_plan_divergence_warning() -> None:
    from datetime import date, timedelta

    import polars as pl

    from dce.ingest.checks import spend_plan_divergence

    first = date(2024, 1, 1)
    weeks = [first + timedelta(weeks=i) for i in range(20)]
    plan = pl.DataFrame({"week_start": weeks, "region_id": ["R"] * 20, "channel": ["D2C"] * 20,
                         "campaign_type": ["ppc"] * 20, "planned_spend_inr": [100.0] * 20})  # fmt: skip
    days = [first + timedelta(days=d) for d in range(140)]
    spend = [50.0 if d < 28 else 0.0 for d in range(140)]  # spend stops after 4 weeks
    mkt = pl.DataFrame({"date": days, "channel": ["D2C"] * 140, "spend_inr": spend})
    issues = spend_plan_divergence(
        {"marketing_daily": mkt, "marketing_plan": plan}, first, weeks[-1]
    )
    assert len(issues) == 1 and issues[0].n_rows == 16 and issues[0].severity == "warning"
    ok = mkt.with_columns(pl.lit(50.0).alias("spend_inr"))
    assert (
        spend_plan_divergence({"marketing_daily": ok, "marketing_plan": plan}, first, weeks[-1])
        == []
    )

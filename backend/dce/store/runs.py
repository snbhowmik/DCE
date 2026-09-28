"""Run lifecycle: id generation, provenance capture, status updates."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from dce import paths
from dce.gitinfo import git_sha
from dce.hashing import config_hash, file_sha256
from dce.store.db import session_scope
from dce.store.models import Run, RunArtifact, utcnow


def new_run_id(now: datetime | None = None) -> str:
    """`run_<UTC yyyymmddThhmmss>_<8 hex>`; sortable by creation time, unique per execution."""
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S")
    return f"run_{ts}_{secrets.token_hex(4)}"


def run_dir(run_id: str) -> Path:
    return paths.PROCESSED_DIR / "runs" / run_id


def start_run(
    engine: Engine,
    *,
    dataset_hash: str,
    config: Mapping[str, Any],
    mode: str,
    seed: int,
    kind: str = "plan",
    parent_run_id: str | None = None,
) -> Run:
    run = Run(
        run_id=new_run_id(),
        dataset_hash=dataset_hash,
        config_hash=config_hash(config),
        git_sha=git_sha(),
        mode=mode,
        seed=seed,
        kind=kind,
        parent_run_id=parent_run_id,
        status="running",
        config=dict(config),
    )
    with session_scope(engine) as s:
        s.add(run)
    return run


def finish_run(engine: Engine, run_id: str, *, error: str | None = None) -> Run:
    with session_scope(engine) as s:
        run = s.get(Run, run_id)
        if run is None:
            raise KeyError(run_id)
        run.status = "failed" if error else "succeeded"
        run.error = error
        run.finished_at = utcnow()
        s.add(run)
    return run


def add_artifact(engine: Engine, run_id: str, kind: str, path: Path) -> RunArtifact:
    art = RunArtifact(run_id=run_id, kind=kind, path=str(path), sha256=file_sha256(path))
    with session_scope(engine) as s:
        s.add(art)
    return art

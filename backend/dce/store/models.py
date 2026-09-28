"""SQLite app state (ARCH §5.12). Large artifacts live in Parquet; rows hold paths + hashes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import JSON, Column, Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class Dataset(SQLModel, table=True):
    __tablename__ = "datasets"

    dataset_hash: str = Field(primary_key=True)
    world_id: str
    source_path: str
    contract_version: str | None = None
    n_errors: int = 0
    n_warnings: int = 0
    report_path: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    run_id: str = Field(primary_key=True)
    dataset_hash: str = Field(index=True)
    config_hash: str
    git_sha: str
    mode: str
    seed: int
    kind: str = "plan"  # plan | scenario | onboarding | eval | dummy
    parent_run_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: str = "pending"  # pending | running | succeeded | failed
    error: str | None = None
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class RunArtifact(SQLModel, table=True):
    __tablename__ = "run_artifacts"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.run_id")
    kind: str  # forecast | capacity | allocation | stress | alerts | narrative | ...
    path: str
    sha256: str | None = None


class Recommendation(SQLModel, table=True):
    __tablename__ = "recommendations"

    rec_id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.run_id")
    kind: str  # allocation | spend | mitigation | onboarding
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class Decision(SQLModel, table=True):
    __tablename__ = "decisions"

    id: int | None = Field(default=None, primary_key=True)
    rec_id: str = Field(index=True, foreign_key="recommendations.rec_id")
    action: str  # accept | reject
    reason: str
    user: str
    ts: datetime = Field(default_factory=utcnow)


class Outcome(SQLModel, table=True):
    __tablename__ = "outcomes"

    id: int | None = Field(default=None, primary_key=True)
    rec_id: str = Field(index=True, foreign_key="recommendations.rec_id")
    metric: str
    predicted: float | None = None
    realized: float | None = None
    source_dataset_hash: str | None = None
    ts: datetime = Field(default_factory=utcnow)


class Scenario(SQLModel, table=True):
    __tablename__ = "scenarios"

    scenario_id: str = Field(primary_key=True)
    base_run_id: str = Field(index=True, foreign_key="runs.run_id")
    result_run_id: str | None = None
    text: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "pending"  # pending | ok | unsupported | failed
    created_at: datetime = Field(default_factory=utcnow)

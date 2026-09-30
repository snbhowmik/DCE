"""FastAPI app (ARCH §6, `/api/v1`): serves persisted run payloads, runs new plans in background.

Every response carries `run_id` and/or `dataset_hash` (NFR-3). Run execution is an in-process,
single-worker queue (T8.2): runs are CPU-heavy and deterministic, so one at a time is enough.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlmodel import Session, select

from dce import __version__
from dce.onboarding.simulate import Candidate
from dce.scenario import Levers
from dce.store.db import make_engine
from dce.store.models import Dataset, Decision, Recommendation, Run
from dce.strategy import resolve_mode

SECTIONS = {
    "run",
    "kpis",
    "demand",
    "capacity",
    "plan",
    "stress",
    "risk",
    "markets",
    "accounts",
    "health",
}
DEFAULT_MODES = ["STABILITY", "GROWTH", "D2C_EXPANSION"]

app = FastAPI(title="Biokraft Demand-Capacity Engine", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


# ------------------------------------------------------------------ payload access


@lru_cache(maxsize=64)
def _read_payload(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_payload(engine: Engine, run_id: str) -> dict[str, Any]:
    from dce.service import payload_path

    p = payload_path(engine, run_id)
    if p is None or not p.exists():
        raise HTTPException(404, f"no payload for run {run_id!r}")
    return _read_payload(str(p), p.stat().st_mtime)


# ------------------------------------------------------------------ datasets and runs


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__}


def is_fixture(d: Dataset) -> bool:
    """Unit-test fixtures are never shown as results (TASK.md rule 6)."""
    return d.source_path.replace("\\", "/").startswith("data/fixtures")


@app.get("/api/v1/datasets")
def datasets(
    include_fixtures: bool = False, engine: Engine = Depends(get_engine)
) -> list[dict[str, Any]]:
    from dce.service import latest_runs, validation_report

    runs: dict[str, list[dict[str, Any]]] = {}
    for run, world in latest_runs(engine):
        runs.setdefault(world, []).append(_run_row(run, world))
    with Session(engine) as s:
        rows = s.exec(
            select(Dataset).order_by(Dataset.world_id, Dataset.created_at.desc())  # type: ignore[attr-defined]
        ).all()
    names = world_names()
    out, seen = [], set()
    for d in rows:
        if d.world_id in seen or d.n_errors or (is_fixture(d) and not include_fixtures):
            continue
        seen.add(d.world_id)
        rep = validation_report(d.dataset_hash) or {}
        out.append(
            {
                "dataset_hash": d.dataset_hash,
                "world_id": d.world_id,
                "name": names.get(d.world_id),
                "contract_version": d.contract_version,
                "n_errors": d.n_errors,
                "n_warnings": d.n_warnings,
                "history": rep.get("history"),
                "runs": sorted(runs.get(d.world_id, []), key=lambda r: r["mode"]),
            }
        )
    return out


def _run_row(run: Run, world: str) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "world_id": world,
        "dataset_hash": run.dataset_hash,
        "mode": run.mode,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
    }


@app.get("/api/v1/runs")
def list_runs(
    world: str | None = None, mode: str | None = None, engine: Engine = Depends(get_engine)
) -> list[dict[str, Any]]:
    """Latest succeeded run per (world, mode), optionally filtered."""
    from dce.service import latest_runs

    return [
        _run_row(r, w)
        for r, w in latest_runs(engine)
        if (world is None or w == world) and (mode is None or r.mode == mode)
    ]


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    return load_payload(engine, run_id)


@app.get("/api/v1/runs/{run_id}/{section}")
def get_section(run_id: str, section: str, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    if section == "narrative":
        return narrative(run_id, engine)
    if section not in SECTIONS:
        raise HTTPException(404, f"unknown section {section!r}; one of {sorted(SECTIONS)}")
    p = load_payload(engine, run_id)
    return {"run_id": run_id, "dataset_hash": p["run"]["dataset_hash"], section: p[section]}


def narrative(run_id: str, engine: Engine) -> dict[str, Any]:
    from dce.ai.narrative import cached_brief

    p = load_payload(engine, run_id)
    return {"run_id": run_id, **cached_brief(p)}


@app.get("/api/v1/compare")
def compare(
    world: str,
    modes: list[str] = Query(default=DEFAULT_MODES),
    engine: Engine = Depends(get_engine),
) -> dict[str, Any]:
    """Side-by-side of the latest run of each mode on one world (PRD FR-20)."""
    from dce.service import latest_runs

    latest = {r.mode: r for r, w in latest_runs(engine) if w == world}
    if not latest:
        raise HTTPException(404, f"no runs for world {world!r}")
    out = []
    for m in modes:
        if m not in latest:
            continue
        p = load_payload(engine, latest[m].run_id)
        by_cm: dict[tuple[str, int], float] = {}
        for r in p["plan"]["allocation"]:
            k = (r["channel"], r["month"])
            by_cm[k] = by_cm.get(k, 0.0) + (r["allocated_kg"] or 0.0)
        out.append(
            {
                "mode": m,
                "run_id": p["run"]["run_id"],
                "forecast_hash": p["run"]["forecast_hash"],
                "kpis": p["kpis"],
                "stress": p["stress"]["summary"],
                "allocation": [
                    {"channel": c, "month": mo, "allocated_kg": v}
                    for (c, mo), v in sorted(by_cm.items())
                ],
                "alerts": p["risk"]["alerts"],
                "mode_config": p["run"]["mode_config"],
            }
        )
    return {
        "world_id": world,
        "dataset_hash": next(iter(latest.values())).dataset_hash,
        "modes": out,
    }


# ------------------------------------------------------------------ what-if scenarios (T9.3)


def world_names() -> dict[str, str]:
    import yaml

    from dce import paths

    p = paths.CONFIG_DIR / "worlds.yaml"
    return (
        {str(k): str(v) for k, v in (yaml.safe_load(p.read_text()) or {}).items()}
        if p.exists()
        else {}
    )


class ScenarioRequest(BaseModel):
    world_id: str
    levers: Levers = Field(default_factory=Levers)


@app.post("/api/v1/scenarios", status_code=201)
def create_scenario(req: ScenarioRequest, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    """Apply what-if levers to the world's cached forecast/capacity and re-solve (seconds)."""
    from dce.service import dataset_for_world, run_scenario

    try:
        resolve_mode(req.levers.mode, req.levers.mode_overrides)
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, f"invalid strategy: {exc}") from exc
    try:
        dataset_for_world(engine, req.world_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        return run_scenario(engine, req.world_id, req.levers)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class OnboardingRequest(BaseModel):
    world_id: str
    mode: str = "STABILITY"
    candidate: Candidate
    earliest_start: int = Field(default=0, ge=0, le=11)
    max_start: int | None = Field(default=None, ge=0, le=11)


@app.post("/api/v1/onboarding/simulate")
def onboarding(req: OnboardingRequest, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    """Accept / defer / phase / decline a candidate B2B account (ARCH §5.10, FR-21)."""
    from dce.service import dataset_for_world, simulate_onboarding

    try:
        resolve_mode(req.mode)
        dataset_for_world(engine, req.world_id)
    except LookupError as exc:
        raise HTTPException(404 if "dataset" in str(exc) else 422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return simulate_onboarding(
        engine, req.world_id, req.mode, req.candidate, req.earliest_start, req.max_start
    )


# ------------------------------------------------------------------ background runs (T8.2)


class RunRequest(BaseModel):
    world_id: str
    modes: list[str] = Field(default_factory=lambda: list(DEFAULT_MODES))
    seed: int | None = None


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)


def _execute(job_id: str, req: RunRequest, engine: Engine) -> None:
    from dce.service import run_modes

    def started(run_id: str, mode: str) -> None:
        with _jobs_lock:
            _jobs[job_id]["runs"].append({"run_id": run_id, "mode": mode})

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
    try:
        recs = run_modes(engine, req.world_id, req.modes, req.seed, on_start=started)
        status = "succeeded" if all(r.status == "succeeded" for r in recs) else "failed"
        with _jobs_lock:
            _jobs[job_id].update(
                status=status, runs=[{"run_id": r.run_id, "mode": r.mode, "status": r.status}
                                     for r in recs]
            )  # fmt: skip
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id].update(status="failed", error=repr(exc))


@app.post("/api/v1/runs", status_code=202)
def create_run(req: RunRequest, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    from dce.service import dataset_for_world

    for m in req.modes:
        try:
            resolve_mode(m)
        except (LookupError, ValueError) as exc:
            raise HTTPException(422, f"invalid mode {m!r}") from exc
    try:
        ds = dataset_for_world(engine, req.world_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    job_id = f"job_{len(_jobs) + 1:04d}"
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "world_id": req.world_id,
            "dataset_hash": ds.dataset_hash,
            "modes": req.modes,
            "status": "queued",
            "runs": [],
        }
    _executor.submit(_execute, job_id, req, engine)
    return _jobs[job_id]


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(404, f"unknown job {job_id!r}")
        return dict(_jobs[job_id])


# ------------------------------------------------------------------ decision log (PRD FR-30)


class DecisionIn(BaseModel):
    run_id: str
    kind: str = Field(pattern="^(allocation|spend|coman|mitigation|onboarding|alert)$")
    key: str = Field(min_length=1, max_length=200)  # what is decided, e.g. "coman:COMAN-PUN-01:1"
    summary: str = Field(min_length=1, max_length=500)
    action: str = Field(pattern="^(accept|reject)$")
    reason: str = Field(min_length=3, max_length=2000)
    user: str = Field(default="demo", min_length=1, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/v1/decisions", status_code=201)
def create_decision(d: DecisionIn, engine: Engine = Depends(get_engine)) -> dict[str, Any]:
    load_payload(engine, d.run_id)  # 404 for unknown runs
    rec_id = f"{d.run_id}:{d.kind}:{d.key}"
    with Session(engine) as s:
        if s.get(Recommendation, rec_id) is None:
            s.add(
                Recommendation(
                    rec_id=rec_id, run_id=d.run_id, kind=d.kind, summary=d.summary,
                    payload=d.details,
                )
            )  # fmt: skip
            s.commit()
        dec = Decision(rec_id=rec_id, action=d.action, reason=d.reason, user=d.user)
        s.add(dec)
        s.commit()
        s.refresh(dec)
        return _decision_row(dec, s.get(Recommendation, rec_id))


def _decision_row(dec: Decision, rec: Recommendation | None) -> dict[str, Any]:
    return {
        "id": dec.id,
        "rec_id": dec.rec_id,
        "run_id": rec.run_id if rec else None,
        "kind": rec.kind if rec else None,
        "summary": rec.summary if rec else None,
        "action": dec.action,
        "reason": dec.reason,
        "user": dec.user,
        "ts": dec.ts.isoformat(),
    }


@app.get("/api/v1/decisions")
def list_decisions(
    run_id: str | None = None, engine: Engine = Depends(get_engine)
) -> list[dict[str, Any]]:
    with Session(engine) as s:
        q = select(Decision, Recommendation).join(
            Recommendation,
            Recommendation.rec_id == Decision.rec_id,  # type: ignore[arg-type]
        )
        if run_id:
            q = q.where(Recommendation.run_id == run_id)
        rows = s.exec(q.order_by(Decision.ts.desc())).all()  # type: ignore[attr-defined]
        return [_decision_row(d, r) for d, r in rows]

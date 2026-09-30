"""Run execution shared by the CLI and the API: run the pipeline, record it, persist its payload.

`run_modes` computes the mode-independent stages once per dataset and plans every requested mode
on top of them (P1), so precomputing all modes of a world costs one forecast.
"""

from __future__ import annotations

import json
import pickle
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import Engine
from sqlmodel import Session, select

from dce.ingest import load_dataset, load_history_window
from dce.ingest.core import processed_path
from dce.payload import build_payload, clean, write_payload
from dce.runner import UpstreamOutputs, build_run_config, plan_stage, upstream_stage
from dce.scenario import Levers, apply_levers
from dce.store.models import Dataset, Run, RunArtifact
from dce.store.runs import add_artifact, finish_run, run_dir, start_run

PAYLOAD_KIND = "payload"


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    world_id: str
    mode: str
    status: str
    payload_path: Path | None


def dataset_for_world(engine: Engine, world_id: str) -> Dataset:
    with Session(engine) as s:
        ds = s.exec(
            select(Dataset)
            .where(Dataset.world_id == world_id, Dataset.n_errors == 0)
            .order_by(Dataset.created_at.desc())  # type: ignore[attr-defined]
        ).first()
    if ds is None:
        raise LookupError(f"no valid ingested dataset for {world_id!r}; run `dce ingest` first")
    return ds


def validation_report(dataset_hash: str) -> dict[str, Any] | None:
    p = processed_path(dataset_hash) / "validation_report.json"
    return json.loads(p.read_text()) if p.exists() else None


def run_modes(
    engine: Engine,
    world_id: str,
    modes: Iterable[str],
    seed: int | None = None,
    on_start: Callable[[str, str], None] | None = None,
    app_overrides: dict[str, Any] | None = None,
) -> list[RunRecord]:
    """Plan every mode for one world; returns one record per mode (failed runs included)."""
    modes = list(modes)
    ds = dataset_for_world(engine, world_id)
    tables = load_dataset(ds.dataset_hash)
    window = load_history_window(ds.dataset_hash, tables)
    validation = validation_report(ds.dataset_hash)
    t0 = time.time()
    ov = {"app": app_overrides} if app_overrides else {}
    up = upstream_stage(tables, window, build_run_config(modes[0], seed, **ov))
    upstream_s = time.time() - t0
    save_upstream(ds.dataset_hash, up)  # scenarios re-solve on exactly this forecast
    out: list[RunRecord] = []
    for mode in modes:
        run = build_run_config(mode, seed, **ov)
        rec = start_run(
            engine, dataset_hash=ds.dataset_hash, config=run.as_dict(), mode=mode, seed=run.seed
        )
        if on_start:
            on_start(rec.run_id, mode)
        t1 = time.time()
        try:
            res = plan_stage(tables, window, run, up)
            meta = {
                "run_id": rec.run_id,
                "world_id": world_id,
                "dataset_hash": ds.dataset_hash,
                "config_hash": rec.config_hash,
                "git_sha": rec.git_sha,
                "created_at": rec.created_at,
                "timing_s": {"upstream": upstream_s, "plan": time.time() - t1},
            }
            path = write_payload(
                build_payload(res, tables, meta, validation),
                run_dir(rec.run_id) / "payload.json",
            )
            add_artifact(engine, rec.run_id, PAYLOAD_KIND, path)
        except Exception as exc:
            finish_run(engine, rec.run_id, error=repr(exc))
            out.append(RunRecord(rec.run_id, world_id, mode, "failed", None))
            continue
        finish_run(engine, rec.run_id)
        out.append(RunRecord(rec.run_id, world_id, mode, "succeeded", path))
    return out


def payload_path(engine: Engine, run_id: str) -> Path | None:
    with Session(engine) as s:
        art = s.exec(
            select(RunArtifact).where(
                RunArtifact.run_id == run_id, RunArtifact.kind == PAYLOAD_KIND
            )
        ).first()
    return Path(art.path) if art else None


def latest_runs(engine: Engine) -> list[tuple[Run, str]]:
    """Latest succeeded run with a payload per (world, mode): [(run, world_id)]."""
    with Session(engine) as s:
        rows = s.exec(
            select(Run, Dataset.world_id)
            .join(Dataset, Dataset.dataset_hash == Run.dataset_hash)  # type: ignore[arg-type]
            .join(RunArtifact, RunArtifact.run_id == Run.run_id)  # type: ignore[arg-type]
            .where(Run.status == "succeeded", Run.kind == "plan", RunArtifact.kind == PAYLOAD_KIND)
            .order_by(Run.created_at.desc())  # type: ignore[attr-defined]
        ).all()
    seen: set[tuple[str, str]] = set()
    out = []
    for run, world in rows:
        if (world, run.mode) not in seen:
            seen.add((world, run.mode))
            out.append((run, world))
    return out


# ------------------------------------------------------------------ upstream cache + scenarios

_upstream: dict[str, UpstreamOutputs] = {}
_upstream_lock = threading.Lock()


def _upstream_file(dataset_hash: str) -> Path:
    return processed_path(dataset_hash) / "upstream.pkl"


def save_upstream(dataset_hash: str, up: UpstreamOutputs) -> None:
    """Mode-independent stages, pickled next to the dataset (written by our own pipeline only)."""
    p = _upstream_file(dataset_hash)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(up, protocol=pickle.HIGHEST_PROTOCOL))
    tmp.replace(p)
    _upstream[dataset_hash] = up


def load_upstream(dataset_hash: str, tables: dict[str, Any], window: Any) -> UpstreamOutputs:
    with _upstream_lock:
        if dataset_hash in _upstream:
            return _upstream[dataset_hash]
        p = _upstream_file(dataset_hash)
        if p.exists():
            up = pickle.loads(p.read_bytes())
        else:
            up = upstream_stage(tables, window, build_run_config("STABILITY"))
            save_upstream(dataset_hash, up)
        if len(_upstream) >= 4:
            _upstream.pop(next(iter(_upstream)))
        _upstream[dataset_hash] = up
        return up


def solver_for(engine: Engine, world_id: str) -> Any:
    """`solve(levers) -> PipelineOutputs` on the world's cached upstream (nothing persisted)."""
    ds = dataset_for_world(engine, world_id)
    tables = load_dataset(ds.dataset_hash)
    window = load_history_window(ds.dataset_hash, tables)
    up0 = load_upstream(ds.dataset_hash, tables, window)

    def solve(lv: Levers) -> Any:
        up, t2 = apply_levers(lv, up0, tables)
        return plan_stage(
            t2, window, build_run_config(lv.mode, None, lv.mode_overrides), up, lv.candidate
        )

    return solve


def mitigations_for(engine: Engine, run_id: str) -> dict[str, Any]:
    """Ranked mitigations for a run's alerts, cached next to its payload."""
    from dce.mitigate.rank import evaluate

    path = run_dir(run_id) / "mitigations.json"
    if path.exists():
        return json.loads(path.read_text())
    pp = payload_path(engine, run_id)
    if pp is None:
        raise LookupError(f"no payload for run {run_id!r}")
    p = json.loads(pp.read_text())
    t0 = time.time()
    out = evaluate(p, solver_for(engine, p["run"]["world_id"]))
    out |= {"run_id": run_id, "seconds": round(time.time() - t0, 1)}
    path.write_text(json.dumps(clean(out)))
    return out


def run_scenario(engine: Engine, world_id: str, levers: Levers) -> dict[str, Any]:
    """Apply levers to the world's cached upstream, re-solve, stress-test and persist the result
    as a `scenario` run whose parent is the latest plan run of the same world and mode."""
    ds = dataset_for_world(engine, world_id)
    base = next(
        (r for r, w in latest_runs(engine) if w == world_id and r.mode == levers.mode), None
    )
    tables = load_dataset(ds.dataset_hash)
    window = load_history_window(ds.dataset_hash, tables)
    up, tables2 = apply_levers(levers, load_upstream(ds.dataset_hash, tables, window), tables)
    run = build_run_config(levers.mode, None, levers.mode_overrides)
    rec = start_run(
        engine,
        dataset_hash=ds.dataset_hash,
        config=run.as_dict() | {"levers": levers.model_dump()},
        mode=levers.mode,
        seed=run.seed,
        kind="scenario",
        parent_run_id=base.run_id if base else None,
    )
    t0 = time.time()
    try:
        res = plan_stage(tables2, window, run, up, levers.candidate)
        meta = {
            "run_id": rec.run_id,
            "world_id": world_id,
            "dataset_hash": ds.dataset_hash,
            "config_hash": rec.config_hash,
            "git_sha": rec.git_sha,
            "created_at": rec.created_at,
            "timing_s": {"upstream": 0.0, "plan": time.time() - t0},
            "kind": "scenario",
            "parent_run_id": rec.parent_run_id,
            "levers": levers.model_dump(),
            "changes": levers.changes(),
        }
        path = write_payload(
            build_payload(res, tables2, meta, validation_report(ds.dataset_hash)),
            run_dir(rec.run_id) / "payload.json",
        )
        add_artifact(engine, rec.run_id, PAYLOAD_KIND, path)
    except Exception as exc:
        finish_run(engine, rec.run_id, error=repr(exc))
        raise
    finish_run(engine, rec.run_id)
    return {
        "run_id": rec.run_id,
        "parent_run_id": rec.parent_run_id,
        "world_id": world_id,
        "mode": levers.mode,
        "changes": levers.changes(),
        "seconds": round(time.time() - t0, 2),
    }


# ------------------------------------------------------------------ onboarding simulator (T7.1)


def _option_metrics(res: Any, cand: Any = None) -> dict[str, Any]:
    from dce.onboarding.simulate import schedule

    s = res.stress.summary()
    plan, inp = res.plan, res.inputs
    n_old = len(inp.accounts) - (1 if cand is not None else 0)
    commit_old = float(inp.commit[:n_old].sum())
    out = {
        "revenue_inr": s["revenue_inr"]["mean"],
        "contribution_inr": s["contribution_inr"]["mean"],
        "b2b_fill_rate": s["b2b_fill_rate"]["mean"],
        "d2c_fill_rate": s["d2c_fill_rate"]["mean"],
        "p_any_b2b_shortfall": s["any_b2b_shortfall"]["mean"],
        "waste_kg": s["waste_kg"]["mean"],
        "b2b_fill_existing": float(plan.y[:n_old].sum()) / commit_old if commit_old else 1.0,
        "d2c_allocated_kg": float(plan.x.sum()),
        "n_breach_alerts": sum(a.kind == "breach" for a in res.risk.alerts),
    }
    if cand is not None:
        sch = schedule(cand, inp.M)
        out["candidate_fill"] = float(plan.y[-1].sum()) / float(sch.sum()) if sch.sum() else 1.0
        out["capacity_share"] = float(np.max(sch / np.maximum(inp.cap_in, 1e-9)))
    return out


def simulate_onboarding(
    engine: Engine,
    world_id: str,
    mode: str,
    candidate: Any,
    earliest_start: int = 0,
    max_start: int | None = None,
) -> dict[str, Any]:
    """Re-solve with the candidate for every start month × ramp; recommend; persist the chosen
    option as a scenario run so it can be opened on every screen."""
    from dce.onboarding.simulate import RAMPS, recommend

    ds = dataset_for_world(engine, world_id)
    tables = load_dataset(ds.dataset_hash)
    window = load_history_window(ds.dataset_hash, tables)
    up = load_upstream(ds.dataset_hash, tables, window)
    run = build_run_config(mode)
    t0 = time.time()
    base_res = plan_stage(tables, window, run, up)
    M = base_res.inputs.M
    base = _option_metrics(base_res)
    last = M - 1 if max_start is None else min(max_start, M - 1)
    options = []
    for start in range(min(earliest_start, last), last + 1):
        for ramp in RAMPS:
            if ramp != "full" and start == M - 1:
                continue  # a ramp needs at least two months inside the horizon
            cand = candidate.model_copy(update={"start_month": start, "ramp": ramp})
            m = _option_metrics(plan_stage(tables, window, run, up, cand), cand)
            m |= {
                "start_month": start,
                "ramp": ramp,
                "delta": {k: m[k] - base[k] for k in base if isinstance(base[k], float)},
            }
            options.append(m)
    rec = recommend(base, options, mode, base_res.inputs.concentration_cap)
    best = rec["option"]
    chosen = candidate.model_copy(update={"start_month": best["start_month"], "ramp": best["ramp"]})
    scen = run_scenario(engine, world_id, Levers(mode=mode, candidate=chosen))
    return {
        "world_id": world_id,
        "mode": mode,
        "base": base,
        "options": options,
        "recommendation": {k: v for k, v in rec.items() if k != "option"}
        | {"start_month": best["start_month"], "ramp": best["ramp"]},
        "run_id": scen["run_id"],
        "seconds": round(time.time() - t0, 1),
    }

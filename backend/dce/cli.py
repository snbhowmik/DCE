"""`dce` command-line entry point."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dce import __version__, paths

app = typer.Typer(no_args_is_help=True, help="Biokraft Demand-Capacity Engine")


@app.callback()
def main() -> None:
    """Biokraft Demand-Capacity Engine."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command("ingest")
def ingest_cmd(
    world_path: Path,
    as_json: bool = typer.Option(False, "--json", help="Print the full JSON report."),
) -> None:
    """Validate a world folder, write the report and Parquet, and register the dataset."""
    from dce.ingest import PathNotAllowed, ingest
    from dce.store.db import make_engine

    try:
        report = ingest(world_path, engine=make_engine())
    except (PathNotAllowed, FileNotFoundError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        typer.echo(report.to_markdown())
        typer.echo(f"report: data/processed/{report.dataset_hash}/validation_report.json")
    raise typer.Exit(0 if report.ok else 1)


@app.command("run")
def run_cmd(
    world: str = typer.Option(..., "--world", help="world_id of an ingested dataset"),
    mode: str = typer.Option("STABILITY", "--mode"),
    seed: int | None = typer.Option(None, "--seed"),
) -> None:
    """Forecast → capacity → response → plan for one ingested world, recorded as a run."""
    import time

    import polars as pl
    from sqlmodel import Session, select

    from dce.ingest import load_dataset, load_history_window
    from dce.runner import build_run_config, run_pipeline
    from dce.store.db import make_engine
    from dce.store.models import Dataset
    from dce.store.runs import finish_run, start_run

    engine = make_engine()
    with Session(engine) as s:
        ds = s.exec(select(Dataset).where(Dataset.world_id == world, Dataset.n_errors == 0)).first()
    if ds is None:
        msg = f"error: no valid ingested dataset for {world!r}; run `dce ingest` first"
        typer.echo(msg, err=True)
        raise typer.Exit(2)
    run = build_run_config(mode, seed)
    rec = start_run(
        engine, dataset_hash=ds.dataset_hash, config=run.as_dict(), mode=mode, seed=run.seed
    )
    t0 = time.time()
    try:
        tables = load_dataset(ds.dataset_hash)
        out = run_pipeline(tables, load_history_window(ds.dataset_hash, tables), run)
    except Exception as exc:
        finish_run(engine, rec.run_id, error=repr(exc))
        raise
    finish_run(engine, rec.run_id)
    typer.echo(f"run {rec.run_id} · {world} · {mode} · {out.plan.status} · {time.time() - t0:.1f}s")
    alloc = out.plan.allocation()
    typer.echo(
        alloc.group_by("channel", "month")
        .agg(pl.col("demand_kg", "allocated_kg", "unmet_kg").sum().round(0))
        .sort("channel", "month")
    )
    typer.echo(
        out.comparison.means()
        .select(
            "plan",
            "revenue_inr",
            "contribution_inr",
            "d2c_fill_rate",
            "b2b_fill_rate",
            "any_b2b_shortfall",
            "waste_kg",
        )
        .with_columns(pl.col(pl.Float64).round(3))
    )
    typer.echo(
        out.stress.summary_frame()
        .filter(
            pl.col("metric").is_in(
                ["revenue_inr", "d2c_fill_rate", "b2b_fill_rate", "any_b2b_shortfall", "waste_kg"]
            )
        )
        .with_columns(pl.col("mean", "p10", "p50", "p90").round(3))
    )
    if "coman" in out.plan.extras:
        typer.echo(out.plan.extras["coman"].filter(pl.col("active")))
    r = out.risk
    typer.echo(f"risk: breach θ={r.breach_threshold:.2f} · {len(r.alerts)} alert(s)")
    for a in r.alerts:
        typer.echo(
            f"  {a.kind:<7} {a.start}…{a.end} in {a.weeks_until} wk · "
            f"peak p={a.peak_probability:.2f} · E[kg]={a.expected_kg:,.0f}"
        )


WORLDS_OPT = typer.Option(None, "--world", help="world_id (repeatable); default: all")
MODES_OPT = typer.Option(
    None, "--mode", help="mode (repeatable); default: STABILITY, GROWTH, D2C_EXPANSION"
)


@app.command("precompute")
def precompute_cmd(
    worlds: list[str] = WORLDS_OPT,
    modes: list[str] = MODES_OPT,
    seed: int | None = typer.Option(None, "--seed"),
) -> None:
    """Run and persist payloads for worlds × modes (forecast computed once per world)."""
    import time

    from sqlmodel import Session, select

    from dce.service import run_modes
    from dce.store.db import make_engine
    from dce.store.models import Dataset

    engine = make_engine()
    modes = modes or ["STABILITY", "GROWTH", "D2C_EXPANSION"]
    if not worlds:
        with Session(engine) as s:
            worlds = sorted(
                {
                    d.world_id
                    for d in s.exec(select(Dataset))
                    if d.n_errors == 0 and not d.source_path.startswith("data/fixtures")
                }
            )
    failed = 0
    for w in worlds:
        t0 = time.time()
        for r in run_modes(engine, w, modes, seed):
            failed += r.status != "succeeded"
            typer.echo(f"{w:<16} {r.mode:<14} {r.status:<10} {r.run_id}")
        typer.echo(f"{w}: {time.time() - t0:.1f}s")
    raise typer.Exit(1 if failed else 0)


@app.command("narrate")
def narrate_cmd(
    pause: float = typer.Option(2.5, help="seconds between LLM calls (rate limits)"),
) -> None:
    """Write (or reuse) the planning brief for the latest run of every world × strategy."""
    import time

    from dce.ai.narrative import cached_brief
    from dce.service import latest_runs, payload_path
    from dce.store.db import make_engine

    engine = make_engine()
    for run, world in latest_runs(engine):
        p = payload_path(engine, run.run_id)
        if p is None:
            continue
        t0 = time.time()
        b = cached_brief(json.loads(p.read_text()))
        typer.echo(f"{world:<16} {run.mode:<14} {b['source']:<8} {b.get('fallback_reason') or ''}")
        if time.time() - t0 > 0.5:
            time.sleep(pause)


contract_app = typer.Typer(no_args_is_help=True, help="Data contract tools.")
app.add_typer(contract_app, name="contract")


@contract_app.command("export")
def contract_export() -> None:
    """Regenerate contract/ (JSON Schemas, README, CONTRACT_VERSION) from the spec."""
    from dce.contract.export import export

    for p in export(paths.CONTRACT_DIR):
        typer.echo(p.relative_to(paths.ROOT))


@contract_app.command("check")
def contract_check(world_dir: Path) -> None:
    """Validate a world folder against the contract (no ingest, no storage)."""
    from dce.contract.validate import validate_world

    result = validate_world(world_dir)
    for issue in result.issues:
        typer.echo(json.dumps(issue.to_dict(), ensure_ascii=False))
    n_err = len(result.errors)
    typer.echo(f"{n_err} error(s), {len(result.issues) - n_err} warning(s)")
    raise typer.Exit(0 if result.ok else 1)


if __name__ == "__main__":
    app()

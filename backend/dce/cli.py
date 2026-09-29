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

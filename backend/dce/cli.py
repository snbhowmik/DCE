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

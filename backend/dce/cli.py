"""`dce` command-line entry point."""

from __future__ import annotations

import typer

from dce import __version__

app = typer.Typer(no_args_is_help=True, help="Biokraft Demand-Capacity Engine")


@app.callback()
def main() -> None:
    """Biokraft Demand-Capacity Engine."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()

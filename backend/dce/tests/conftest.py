from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from dce import paths
from dce.ingest import ingest, load_dataset, load_history_window

TINY = paths.FIXTURES_DIR / "tiny_world"


@pytest.fixture(scope="session")
def tiny_processed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """tiny_world ingested once per session into a temp processed dir."""
    out = tmp_path_factory.mktemp("processed")
    mp = pytest.MonkeyPatch()
    mp.setattr(paths, "PROCESSED_DIR", out)
    report = ingest(TINY)
    assert report.ok
    yield out / report.dataset_hash
    mp.undo()


@pytest.fixture(scope="session")
def tiny_tables(tiny_processed: Path) -> dict[str, pl.DataFrame]:
    return load_dataset_at(tiny_processed)


@pytest.fixture(scope="session")
def tiny_window(tiny_processed: Path, tiny_tables: dict[str, pl.DataFrame]) -> tuple[date, date]:
    mp = pytest.MonkeyPatch()
    mp.setattr(paths, "PROCESSED_DIR", tiny_processed.parent)
    try:
        return load_history_window(tiny_processed.name, tiny_tables)
    finally:
        mp.undo()


def load_dataset_at(processed: Path) -> dict[str, pl.DataFrame]:
    mp = pytest.MonkeyPatch()
    mp.setattr(paths, "PROCESSED_DIR", processed.parent)
    try:
        return load_dataset(processed.name)
    finally:
        mp.undo()

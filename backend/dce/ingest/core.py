"""Ingest a world: guard → contract validation → continuity → report → Parquet (ARCH §5.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import Engine

from dce import paths
from dce.contract.spec import CONTRACT_VERSION, TABLES
from dce.contract.validate import Issue, validate_world
from dce.hashing import dataset_hash
from dce.ingest.checks import continuity_issues, history_window, table_summary
from dce.logs import get_logger
from dce.store.db import session_scope
from dce.store.models import Dataset

CONTRACT_FILES = (*(t.filename for t in TABLES), "manifest.json")
log = get_logger(__name__)


class PathNotAllowed(ValueError):
    """Raised when a world path resolves outside the allowed data roots (ARCH §9.5)."""


def allowed_roots() -> tuple[Path, ...]:
    return (paths.INCOMING_DIR.resolve(), paths.FIXTURES_DIR.resolve())


def guard_path(world_path: str | Path) -> Path:
    """Resolve `world_path` (following symlinks) and require it strictly inside an allowed root."""
    resolved = Path(world_path).expanduser().resolve()
    for root in allowed_roots():
        if resolved != root and resolved.is_relative_to(root):
            if not resolved.is_dir():
                raise FileNotFoundError(f"world folder not found: {world_path}")
            return resolved
    roots = ", ".join(_display_path(r) for r in allowed_roots())
    raise PathNotAllowed(f"refusing {world_path!s}: data may only be read from {roots}")


@dataclass
class RawDataset:
    world_id: str
    path: Path
    dataset_hash: str
    manifest: dict[str, Any] | None
    tables: dict[str, pl.DataFrame]
    contract_issues: list[Issue] = field(default_factory=list)


@dataclass
class ValidationReport:
    dataset_hash: str
    world_id: str
    source_path: str
    contract_version: str
    manifest_contract_version: str | None
    history: dict[str, str] | None
    tables: dict[str, dict[str, Any]]
    issues: list[Issue]
    created_at: str

    @property
    def n_errors(self) -> int:
        return sum(i.severity == "error" for i in self.issues)

    @property
    def n_warnings(self) -> int:
        return sum(i.severity == "warning" for i in self.issues)

    @property
    def ok(self) -> bool:
        return self.n_errors == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_hash": self.dataset_hash,
            "world_id": self.world_id,
            "source_path": self.source_path,
            "contract_version": self.contract_version,
            "manifest_contract_version": self.manifest_contract_version,
            "history": self.history,
            "ok": self.ok,
            "n_errors": self.n_errors,
            "n_warnings": self.n_warnings,
            "tables": self.tables,
            "issues": [i.to_dict() for i in self.issues],
            "created_at": self.created_at,
        }

    def to_markdown(self) -> str:
        verdict = "PASS" if self.ok else "FAIL"
        lines = [
            f"# Validation report: {self.world_id}",
            "",
            f"- **Result:** {verdict} ({self.n_errors} error(s), {self.n_warnings} warning(s))",
            f"- **Dataset hash:** `{self.dataset_hash}`",
            f"- **Source:** `{self.source_path}`",
            f"- **Contract:** app {self.contract_version}, "
            f"manifest {self.manifest_contract_version}",
        ]
        if self.history:
            lines.append(
                f"- **History:** {self.history['first_week']} → {self.history['last_week']} "
                f"({self.history['n_weeks']} weeks)"
            )
        lines += ["", "## Tables", "", "| table | rows | from | to |", "|---|---:|---|---|"]
        for name, t in self.tables.items():
            lines.append(
                f"| {name} | {t['rows']} | {t.get('date_min', '')} | {t.get('date_max', '')} |"
            )
        for sev in ("error", "warning"):
            items = [i for i in self.issues if i.severity == sev]
            if not items:
                continue
            lines += ["", f"## {sev.capitalize()}s", ""]
            for i in items:
                where = ".".join(x for x in (i.table, i.column) if x) or "dataset"
                at = f" (lines {i.lines})" if i.lines else ""
                lines.append(f"- `{where}` [{i.rule}] {i.message}{at}")
        return "\n".join(lines) + "\n"


def load_world(world_path: str | Path) -> RawDataset:
    path = guard_path(world_path)
    result = validate_world(path)
    world_id = (result.manifest or {}).get("world_id") or path.name
    return RawDataset(
        world_id=str(world_id),
        path=path,
        dataset_hash=dataset_hash(path, CONTRACT_FILES),
        manifest=result.manifest,
        tables=result.tables,
        contract_issues=result.issues,
    )


def validate(raw: RawDataset) -> ValidationReport:
    issues = list(raw.contract_issues)
    if not any(i.severity == "error" and i.rule == "missing_column" for i in issues):
        issues += continuity_issues(raw.tables, raw.manifest)
    window = history_window(raw.tables, raw.manifest)
    history = (
        {
            "first_week": str(window[0]),
            "last_week": str(window[1]),
            "n_weeks": str((window[1] - window[0]).days // 7 + 1),
        }
        if window
        else None
    )
    return ValidationReport(
        dataset_hash=raw.dataset_hash,
        world_id=raw.world_id,
        source_path=_display_path(raw.path),
        contract_version=CONTRACT_VERSION,
        manifest_contract_version=(raw.manifest or {}).get("contract_version"),
        history=history,
        tables=table_summary(raw.tables),
        issues=issues,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def _display_path(p: Path) -> str:
    try:
        return str(p.relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def processed_path(dataset_hash: str) -> Path:
    return paths.PROCESSED_DIR / dataset_hash


def ingest(world_path: str | Path, engine: Engine | None = None) -> ValidationReport:
    """Validate a world; always write the report; write Parquet + register only when valid."""
    raw = load_world(world_path)
    report = validate(raw)
    out = processed_path(raw.dataset_hash)
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation_report.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "validation_report.md").write_text(report.to_markdown(), encoding="utf-8")
    if report.ok:
        for name, df in raw.tables.items():
            df.write_parquet(out / f"{name}.parquet")
        if raw.manifest is not None:
            (out / "manifest.json").write_text(json.dumps(raw.manifest, indent=2) + "\n")
    if engine is not None:
        with session_scope(engine) as s:
            s.merge(
                Dataset(
                    dataset_hash=raw.dataset_hash,
                    world_id=raw.world_id,
                    source_path=report.source_path,
                    contract_version=report.manifest_contract_version,
                    n_errors=report.n_errors,
                    n_warnings=report.n_warnings,
                    report_path=_display_path(out / "validation_report.json"),
                )
            )
    log.info(
        "ingest",
        world_id=raw.world_id,
        dataset_hash=raw.dataset_hash,
        ok=report.ok,
        n_errors=report.n_errors,
        n_warnings=report.n_warnings,
    )
    return report


class DatasetNotFound(KeyError):
    pass


def load_dataset(dataset_hash: str) -> dict[str, pl.DataFrame]:
    """Load the validated Parquet tables of an ingested dataset."""
    out = processed_path(dataset_hash)
    if not (out / "orders.parquet").is_file():
        raise DatasetNotFound(f"no valid processed dataset {dataset_hash}")
    return {t.name: pl.read_parquet(out / f"{t.name}.parquet") for t in TABLES}

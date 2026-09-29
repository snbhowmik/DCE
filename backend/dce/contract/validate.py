"""Contract-level validation of one world folder: schema, types, enums, keys, FKs, row rules.

Continuity/coverage checks and the ingest report live in `dce.ingest` (T1.1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema
import pandera.polars as pa
import polars as pl
from pandera.errors import SchemaErrors

from dce.contract.spec import (
    CONTRACT_VERSION,
    MANIFEST_SCHEMA,
    TABLES,
    TABLES_BY_NAME,
    Col,
    Severity,
    Table,
)

_SAMPLE = 5
_POLARS_TYPES: dict[str, type[pl.DataType]] = {
    "str": pl.String,
    "enum": pl.String,
    "float": pl.Float64,
    "int": pl.Int64,
    "bool": pl.Boolean,
    "date": pl.Date,
}


@dataclass
class Issue:
    """One validation finding. `lines` are 1-based CSV line numbers (header = line 1)."""

    table: str | None
    column: str | None
    rule: str
    severity: Severity
    message: str
    n_rows: int = 0
    lines: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "n_rows": self.n_rows,
            "lines": self.lines,
        }


@dataclass
class ContractResult:
    tables: dict[str, pl.DataFrame]
    manifest: dict[str, Any] | None
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _lines(idx: pl.Series | list[int]) -> list[int]:
    return [int(i) + 2 for i in list(idx)[:_SAMPLE]]


def _cast_expr(col: Col) -> pl.Expr:
    raw = pl.col(col.name).str.strip_chars()
    match col.type:
        case "str" | "enum":
            return pl.col(col.name)
        case "float":
            return raw.cast(pl.Float64, strict=False)
        case "int":
            # Accept "3.0" (pandas writes nullable ints as floats) but not "3.5".
            f = raw.cast(pl.Float64, strict=False)
            return pl.when(f == f.floor()).then(f).otherwise(None).cast(pl.Int64)
        case "bool":
            low = raw.str.to_lowercase()
            return (
                pl.when(low.is_in(["true", "1"]))
                .then(True)
                .when(low.is_in(["false", "0"]))
                .then(False)
                .otherwise(None)
            )
        case "date":
            return raw.str.to_date("%Y-%m-%d", strict=False)
    raise ValueError(col.type)


def _pandera_schema(table: Table) -> pa.DataFrameSchema:
    columns = {}
    for c in table.columns:
        checks = []
        if c.enum is not None:
            checks.append(pa.Check.isin(list(c.enum)))
        if c.ge is not None:
            checks.append(pa.Check.ge(c.ge))
        if c.le is not None:
            checks.append(pa.Check.le(c.le))
        columns[c.name] = pa.Column(
            _POLARS_TYPES[c.type], nullable=c.nullable, checks=checks, coerce=False
        )
    return pa.DataFrameSchema(
        columns,
        unique=list(table.primary_key) if table.primary_key else None,
        strict=False,
        name=table.name,
    )


def read_table(path: Path, table: Table) -> tuple[pl.DataFrame | None, list[Issue]]:
    """Read one CSV all-as-string, cast per contract, and run per-table checks."""
    issues: list[Issue] = []
    try:
        raw = pl.read_csv(path, infer_schema=False, raise_if_empty=False)
    except Exception as exc:  # malformed CSV
        return None, [Issue(table.name, None, "unreadable", "error", f"cannot parse CSV: {exc}")]

    missing = [c for c in table.column_names if c not in raw.columns]
    extra = [c for c in raw.columns if c not in table.column_names]
    for name in missing:
        issues.append(
            Issue(table.name, name, "missing_column", "error", f"missing column '{name}'")
        )
    for name in extra:
        issues.append(
            Issue(table.name, name, "extra_column", "warning", f"unknown column '{name}' ignored")
        )
    if missing:
        return None, issues

    raw = raw.select(table.column_names)
    typed = raw.select([_cast_expr(c).alias(c.name) for c in table.columns])

    type_bad: dict[str, set[int]] = {}
    for c in table.columns:
        bad = (
            pl.DataFrame({"raw": raw[c.name], "typed": typed[c.name]})
            .with_row_index()
            .filter(pl.col("raw").is_not_null() & pl.col("typed").is_null())
        )
        if bad.height:
            type_bad[c.name] = set(bad["index"].to_list())
            sample = bad["raw"].head(3).to_list()
            issues.append(
                Issue(
                    table.name,
                    c.name,
                    "type",
                    "error",
                    f"{bad.height} value(s) not parseable as {c.type}, e.g. {sample}",
                    bad.height,
                    _lines(bad["index"]),
                )
            )

    issues += _pandera_issues(typed, table, type_bad)
    issues += _row_rule_issues(typed, table)
    issues += _key_issues(typed, table)
    return typed, issues


def _pandera_issues(df: pl.DataFrame, table: Table, type_bad: dict[str, set[int]]) -> list[Issue]:
    try:
        _pandera_schema(table).validate(df, lazy=True)
        return []
    except SchemaErrors as exc:
        fc = exc.failure_cases
        if not isinstance(fc, pl.DataFrame):
            fc = pl.from_pandas(fc)
    issues = []
    for (column, check), grp in fc.group_by(["column", "check"], maintain_order=True):
        col = str(column) if column is not None else None
        idx = [int(i) for i in grp["index"].to_list() if i is not None]
        check_s = str(check)
        if col and "not_nullable" in check_s:
            idx = [i for i in idx if i not in type_bad.get(col, set())]
            if not idx:
                continue
            rule, msg = "null", f"{len(idx)} null value(s) in non-nullable column"
        elif "isin" in check_s:
            vals = sorted({str(v) for v in grp["failure_case"].to_list()})[:3]
            rule, msg = "enum", f"{len(idx)} value(s) outside allowed set, e.g. {vals}"
        elif "greater_than" in check_s or "less_than" in check_s:
            rule, msg = "range", f"{len(idx)} value(s) violate {check_s}"
        elif "unique" in check_s or "duplicates" in check_s:
            rule, msg = "primary_key", f"{len(idx)} row(s) duplicate primary key"
            col = ",".join(table.primary_key)
        else:
            rule, msg = "schema", f"{check_s}: {grp['failure_case'].head(3).to_list()}"
        issues.append(Issue(table.name, col, rule, "error", msg, len(idx), _lines(sorted(idx))))
    return issues


def _row_rule_issues(df: pl.DataFrame, table: Table) -> list[Issue]:
    issues = []
    indexed = df.with_row_index()
    for rule in table.row_rules:
        bad = indexed.filter(~pl.sql_expr(rule.predicate))
        if bad.height:
            issues.append(
                Issue(
                    table.name,
                    None,
                    rule.name,
                    rule.severity,
                    f"{bad.height} row(s) violate: {rule.description}",
                    bad.height,
                    _lines(bad["index"]),
                )
            )
    for c in table.monday_columns:
        bad = indexed.filter(pl.col(c).dt.weekday() != 1)
        if bad.height:
            issues.append(
                Issue(
                    table.name,
                    c,
                    "monday",
                    "error",
                    f"{bad.height} date(s) are not Mondays",
                    bad.height,
                    _lines(bad["index"]),
                )
            )
    return issues


def _key_issues(df: pl.DataFrame, table: Table) -> list[Issue]:
    if not table.natural_key:
        return []
    dup = df.with_row_index().filter(pl.struct(table.natural_key).is_duplicated())
    if not dup.height:
        return []
    return [
        Issue(
            table.name,
            ",".join(table.natural_key),
            "natural_key",
            "warning",
            f"{dup.height} row(s) share a natural key; they will be summed",
            dup.height,
            _lines(dup["index"]),
        )
    ]


def _fk_issues(tables: dict[str, pl.DataFrame]) -> list[Issue]:
    issues = []
    for t in TABLES:
        df = tables.get(t.name)
        if df is None:
            continue
        for fk in t.foreign_keys:
            ref = tables.get(fk.ref_table)
            if ref is None:
                continue
            keys = ref[fk.ref_column].drop_nulls().unique()
            sub = df.with_row_index()
            if fk.when:
                sub = sub.filter(pl.col(fk.when[0]) == fk.when[1])
            vals = sub.select("index", pl.col(fk.column).alias("v")).drop_nulls("v")
            if fk.sep:
                vals = vals.with_columns(pl.col("v").str.split(fk.sep))
                vals = vals.explode("v", empty_as_null=False)
                vals = vals.with_columns(pl.col("v").str.strip_chars())
            bad = vals.filter(~pl.col("v").is_in(keys.implode()))
            if bad.height:
                where = f" (where {fk.when[0]}={fk.when[1]})" if fk.when else ""
                sample = bad["v"].unique(maintain_order=True).head(3).to_list()
                n = bad["index"].n_unique()
                issues.append(
                    Issue(
                        t.name,
                        fk.column,
                        "foreign_key",
                        "error",
                        f"{n} row(s){where} reference missing "
                        f"{fk.ref_table}.{fk.ref_column}, e.g. {sample}",
                        n,
                        _lines(bad["index"].unique(maintain_order=True)),
                    )
                )
    return issues


def validate_manifest(path: Path, world_dir: Path) -> tuple[dict[str, Any] | None, list[Issue]]:
    if not path.is_file():
        return None, [Issue(None, None, "missing_file", "error", "manifest.json not found")]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [Issue("manifest", None, "unreadable", "error", f"invalid JSON: {exc}")]
    issues = []
    validator = jsonschema.Draft202012Validator(
        MANIFEST_SCHEMA, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.path)):
        loc = ".".join(str(p) for p in err.path) or None
        issues.append(Issue("manifest", loc, "manifest", "error", err.message))
    version = manifest.get("contract_version") if isinstance(manifest, dict) else None
    if isinstance(version, str) and version.count(".") == 2:
        if version.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            issues.append(
                Issue(
                    "manifest",
                    "contract_version",
                    "contract_version",
                    "error",
                    f"major version {version} incompatible with {CONTRACT_VERSION}",
                )
            )
        elif version != CONTRACT_VERSION:
            issues.append(
                Issue(
                    "manifest",
                    "contract_version",
                    "contract_version",
                    "warning",
                    f"version {version} differs from app contract {CONTRACT_VERSION}",
                )
            )
    if isinstance(manifest, dict) and manifest.get("world_id") not in (None, world_dir.name):
        issues.append(
            Issue(
                "manifest",
                "world_id",
                "world_id",
                "warning",
                f"world_id '{manifest.get('world_id')}' differs from folder '{world_dir.name}'",
            )
        )
    try:
        start = date.fromisoformat(manifest["history_start"])
        if date.fromisoformat(manifest["history_end"]) < start:
            issues.append(
                Issue("manifest", "history_end", "manifest", "error", "history_end < history_start")
            )
    except (KeyError, TypeError, ValueError):
        pass
    return manifest, issues


def validate_world(world_dir: Path) -> ContractResult:
    """Validate a world folder against the contract. Does not enforce path guards (see ingest)."""
    manifest, issues = validate_manifest(world_dir / "manifest.json", world_dir)
    tables: dict[str, pl.DataFrame] = {}
    for t in TABLES:
        path = world_dir / t.filename
        if not path.is_file():
            if t.required:
                issues.append(
                    Issue(t.name, None, "missing_file", "error", f"{t.filename} not found")
                )
            continue
        df, t_issues = read_table(path, TABLES_BY_NAME[t.name])
        issues += t_issues
        if df is not None:
            tables[t.name] = df
    issues += _fk_issues(tables)
    return ContractResult(tables=tables, manifest=manifest, issues=issues)

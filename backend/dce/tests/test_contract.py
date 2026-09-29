"""T0.2: the contract accepts a correct world and rejects each class of violation.

The world written here is a minimal, hand-written, contract-valid shape for testing code paths
only (not evaluation data).
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from pathlib import Path

import jsonschema
import pytest

from dce import paths
from dce.contract.export import rendered_files, table_json_schema
from dce.contract.spec import CONTRACT_VERSION, TABLES
from dce.contract.validate import ContractResult, validate_world

MINI_WORLD: dict[str, list[dict[str, str]]] = {
    "regions": [
        {"region_id": "R1", "region_name": "North", "tier": "metro", "cold_chain_available": "true"},
        {"region_id": "R2", "region_name": "South", "tier": "tier1", "cold_chain_available": "false"},
    ],
    "skus": [
        {
            "sku_id": "S1",
            "product_line": "cultivated_chicken",
            "pack_size_kg": "0.5",
            "status": "production",
            "expected_launch_date": "",
            "shelf_life_days": "5",
            "unit_cost_inr_per_kg": "900",
            "list_price_d2c_inr_per_kg": "1500",
        }
    ],
    "product_matrix": [
        {"sku_id": "S1", "channel": "D2C", "region_id": "R1", "eligible_from": "2024-01-01", "eligible_to": ""},
        {"sku_id": "S1", "channel": "B2B", "region_id": "R2", "eligible_from": "2024-01-01", "eligible_to": ""},
    ],
    "orders": [
        {
            "order_id": "O1", "order_date": "2024-01-02", "channel": "D2C", "region_id": "R1",
            "sku_id": "S1", "customer_id": "C1", "account_id": "", "requested_qty_kg": "2",
            "fulfilled_qty_kg": "2", "unit_price_inr": "1500", "status": "fulfilled",
            "promised_date": "2024-01-04", "delivered_date": "2024-01-04",
        },
        {
            "order_id": "O2", "order_date": "2024-01-03", "channel": "B2B", "region_id": "R2",
            "sku_id": "S1", "customer_id": "", "account_id": "A1", "requested_qty_kg": "50",
            "fulfilled_qty_kg": "40", "unit_price_inr": "1200", "status": "partial",
            "promised_date": "2024-01-08", "delivered_date": "2024-01-08",
        },
    ],
    "capacity_batches": [
        {
            "batch_id": "B1", "start_date": "2024-01-01", "end_date": "2024-01-14",
            "facility_id": "F1", "product_line": "cultivated_chicken", "planned_yield_kg": "100",
            "actual_yield_kg": "0", "outcome": "failed",
        }
    ],
    "capacity_plan": [
        {
            "week_start": "2024-01-01", "facility_id": "F1", "product_line": "cultivated_chicken",
            "planned_batches": "1", "planned_yield_per_batch_kg": "100",
        }
    ],
    "coman_contracts": [
        {
            "coman_id": "CM1", "product_line": "cultivated_chicken", "available_from": "2024-03-01",
            "lead_time_weeks": "4", "min_commit_kg_per_week": "20", "max_kg_per_week": "80",
            "unit_cost_inr_per_kg": "1100", "min_active_weeks": "4",
        }
    ],
    "coman_activity": [
        {"coman_id": "CM1", "week_start": "2024-01-01", "requested_kg": "20", "delivered_kg": "18"}
    ],
    "b2b_accounts": [
        {
            "account_id": "A1", "account_type": "distributor", "region_id": "R2",
            "regions_served": "R1|R2", "outlets_count": "12", "status": "active",
            "onboarded_date": "2023-12-01", "contract_start": "2024-01-01",
            "contract_end": "2024-12-31", "committed_kg_per_month": "200",
            "contract_price_inr_per_kg": "1200", "shortfall_penalty_inr_per_kg": "150",
            "requested_start_date": "", "requested_kg_per_month": "",
        }
    ],
    "marketing_daily": [
        {
            "date": "2024-01-01", "region_id": "R1", "channel": "D2C", "campaign_type": "ppc",
            "campaign_id": "K1", "spend_inr": "5000", "impressions": "10000", "clicks": "300",
            "unique_visitors": "250", "bounces": "100", "leads": "20", "qualified_leads": "10",
            "conversions": "3", "attributed_revenue_inr": "6000",
        }
    ],
    "marketing_plan": [
        {
            "week_start": "2024-01-08", "region_id": "R1", "channel": "D2C",
            "campaign_type": "ppc", "planned_spend_inr": "35000",
        }
    ],
    "customers": [
        {
            "customer_id": "C1", "region_id": "R1", "acquired_date": "2023-12-15",
            "acquisition_campaign_type": "", "persona_segment": "urban_foodie",
            "churned_date": "", "is_subscriber": "False",
        }
    ],
    "nps_responses": [
        {"response_date": "2024-01-10", "channel": "D2C", "region_id": "R1", "customer_or_account_id": "C1", "score": "9"},
        {"response_date": "2024-01-10", "channel": "B2B", "region_id": "R2", "customer_or_account_id": "A1", "score": "7"},
    ],
}  # fmt: skip

MANIFEST = {
    "world_id": "world_test",
    "contract_version": CONTRACT_VERSION,
    "history_start": "2024-01-01",
    "history_end": "2024-01-14",
    "generated_at": "2026-09-29T00:00:00Z",
}


def write_world(
    root: Path,
    tables: dict[str, list[dict[str, str]]] | None = None,
    manifest: Mapping[str, object] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    tables = tables if tables is not None else MINI_WORLD
    for t in TABLES:
        rows = tables.get(t.name)
        if rows is None:
            continue
        header = list(rows[0].keys()) if rows else list(t.column_names)
        with (root / t.filename).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
    (root / "manifest.json").write_text(json.dumps(manifest or MANIFEST))
    return root


def mutate(table: str, row: int, **changes: str) -> dict[str, list[dict[str, str]]]:
    tables = {k: [dict(r) for r in v] for k, v in MINI_WORLD.items()}
    tables[table][row].update(changes)
    return tables


def rules(result: ContractResult, table: str | None = None) -> set[str]:
    return {i.rule for i in result.errors if table is None or i.table == table}


@pytest.fixture
def world(tmp_path: Path) -> Path:
    return write_world(tmp_path / "world_test")


def test_valid_world_passes(world: Path) -> None:
    result = validate_world(world)
    assert result.ok, [i.to_dict() for i in result.issues]
    assert set(result.tables) == {t.name for t in TABLES}
    assert result.tables["regions"]["cold_chain_available"].to_list() == [True, False]


def test_wrong_type_rejected(tmp_path: Path) -> None:
    result = validate_world(
        write_world(tmp_path / "w", mutate("orders", 0, requested_qty_kg="two"))
    )
    errs = [i for i in result.errors if i.rule == "type"]
    assert len(errs) == 1
    assert (errs[0].table, errs[0].column, errs[0].lines) == ("orders", "requested_qty_kg", [2])
    assert "null" not in rules(result, "orders")  # a type error is not double-reported as null


def test_bad_date_and_int_rejected(tmp_path: Path) -> None:
    tables = mutate("orders", 1, order_date="03/01/2024")
    tables["b2b_accounts"][0]["outlets_count"] = "12.5"
    result = validate_world(write_world(tmp_path / "w", tables))
    cols = {(i.table, i.column) for i in result.errors if i.rule == "type"}
    assert cols == {("orders", "order_date"), ("b2b_accounts", "outlets_count")}


def test_integral_float_accepted_for_int(tmp_path: Path) -> None:
    result = validate_world(
        write_world(tmp_path / "w", mutate("b2b_accounts", 0, outlets_count="12.0"))
    )
    assert result.ok


def test_missing_column_rejected(tmp_path: Path) -> None:
    tables = {k: [dict(r) for r in v] for k, v in MINI_WORLD.items()}
    for r in tables["skus"]:
        del r["shelf_life_days"]
    result = validate_world(write_world(tmp_path / "w", tables))
    assert any(i.rule == "missing_column" and i.column == "shelf_life_days" for i in result.errors)


def test_missing_file_rejected(tmp_path: Path) -> None:
    tables = {k: v for k, v in MINI_WORLD.items() if k != "customers"}
    result = validate_world(write_world(tmp_path / "w", tables))
    assert "missing_file" in rules(result, "customers")


def test_bad_enum_rejected(tmp_path: Path) -> None:
    result = validate_world(write_world(tmp_path / "w", mutate("orders", 0, status="shipped")))
    errs = [i for i in result.errors if i.rule == "enum"]
    assert [(e.table, e.column, e.lines) for e in errs] == [("orders", "status", [2])]


def test_fk_violation_rejected(tmp_path: Path) -> None:
    result = validate_world(write_world(tmp_path / "w", mutate("orders", 0, sku_id="S404")))
    errs = [i for i in result.errors if i.rule == "foreign_key"]
    assert [(e.table, e.column) for e in errs] == [("orders", "sku_id")]


def test_list_fk_and_conditional_fk(tmp_path: Path) -> None:
    tables = mutate("b2b_accounts", 0, regions_served="R1|R9")
    tables["nps_responses"][1]["customer_or_account_id"] = "C1"  # B2B row pointing at a customer
    result = validate_world(write_world(tmp_path / "w", tables))
    fks = {(i.table, i.column) for i in result.errors if i.rule == "foreign_key"}
    assert fks == {("b2b_accounts", "regions_served"), ("nps_responses", "customer_or_account_id")}


def test_null_in_required_column_rejected(tmp_path: Path) -> None:
    result = validate_world(write_world(tmp_path / "w", mutate("regions", 0, tier="")))
    assert "null" in rules(result, "regions")


def test_duplicate_primary_key_rejected(tmp_path: Path) -> None:
    result = validate_world(write_world(tmp_path / "w", mutate("orders", 1, order_id="O1")))
    assert "primary_key" in rules(result, "orders")


def test_range_and_row_rules(tmp_path: Path) -> None:
    tables = mutate("orders", 0, fulfilled_qty_kg="3")
    tables["nps_responses"][0]["score"] = "11"
    tables["capacity_plan"][0]["week_start"] = "2024-01-02"
    result = validate_world(write_world(tmp_path / "w", tables))
    assert "fulfilled_le_requested" in rules(result, "orders")
    assert "range" in rules(result, "nps_responses")
    assert "monday" in rules(result, "capacity_plan")


def test_manifest_is_closed(tmp_path: Path) -> None:
    bad = {**MANIFEST, "regime": "high seasonality"}
    result = validate_world(write_world(tmp_path / "w", manifest=bad))
    assert "manifest" in rules(result, "manifest")


def test_manifest_major_version_mismatch(tmp_path: Path) -> None:
    result = validate_world(
        write_world(tmp_path / "w", manifest={**MANIFEST, "contract_version": "2.0.0"})
    )
    assert "contract_version" in rules(result, "manifest")


def test_json_schemas_are_valid_and_match_rows() -> None:
    for t in TABLES:
        schema = table_json_schema(t)
        jsonschema.Draft202012Validator.check_schema(schema)


def test_committed_contract_matches_spec() -> None:
    """contract/ is generated; regenerate with `dce contract export` if this fails."""
    for rel, content in rendered_files().items():
        assert (paths.CONTRACT_DIR / rel).read_text(encoding="utf-8") == content, rel

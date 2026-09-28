"""T0.3: tiny_world exercises the code paths it is meant to exercise. Not evaluation data."""

from __future__ import annotations

import polars as pl
import pytest

from dce import paths
from dce.contract.validate import ContractResult, validate_world

TINY = paths.FIXTURES_DIR / "tiny_world"


@pytest.fixture(scope="module")
def tiny() -> ContractResult:
    return validate_world(TINY)


def test_tiny_world_is_valid(tiny: ContractResult) -> None:
    assert tiny.ok, [i.to_dict() for i in tiny.issues]
    assert not tiny.issues


def test_tiny_world_is_labeled_not_for_evaluation() -> None:
    assert (TINY / "NOT_FOR_EVALUATION").is_file()


def test_tiny_world_shape(tiny: ContractResult) -> None:
    t = tiny.tables
    assert t["regions"].height == 2
    assert t["skus"].height == 1
    assert t["b2b_accounts"].height == 3
    assert t["coman_contracts"].height == 2
    weeks = t["orders"]["order_date"].dt.truncate("1w").n_unique()
    assert weeks == 110


def test_tiny_world_has_edge_cases(tiny: ContractResult) -> None:
    orders = tiny.tables["orders"]
    status = set(orders["status"].unique())
    assert {"cancelled_stockout", "waitlisted", "partial"} <= status
    stockout_weeks = (
        orders.filter(pl.col("status") == "cancelled_stockout")["order_date"]
        .dt.truncate("1w")
        .n_unique()
    )
    assert stockout_weeks == 1
    assert tiny.tables["capacity_batches"].filter(pl.col("outcome") == "failed").height == 1
    assert "pipeline" in set(tiny.tables["b2b_accounts"]["status"])

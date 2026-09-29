"""Data contract v1 (ARCH §3): the only interface with the DGP.

This module is the single source of truth. JSON Schemas and `contract/README.md` are generated
from it (`dce contract export`); pandera models are built from it at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CONTRACT_VERSION = "1.0.0"
MIN_HISTORY_WEEKS = 104

ColType = Literal["str", "float", "int", "bool", "date", "enum"]
Severity = Literal["error", "warning"]

CHANNELS = ("D2C", "B2B")
SKU_STATUS = ("rnd", "pilot", "production", "retired")
ORDER_STATUS = ("fulfilled", "partial", "waitlisted", "cancelled_stockout", "cancelled_other")
BATCH_OUTCOME = ("success", "partial", "failed")
ACCOUNT_TYPES = ("restaurant", "qsr_chain", "hotel", "caterer", "distributor", "other")
ACCOUNT_STATUS = ("active", "pipeline", "churned", "paused")
CAMPAIGN_TYPES = ("ppc", "native", "social", "influencer", "email", "trade")


@dataclass(frozen=True)
class Col:
    name: str
    type: ColType
    nullable: bool = False
    enum: tuple[str, ...] | None = None
    ge: float | None = None
    le: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class FK:
    """`column` values must exist in `ref_table.ref_column`.

    `sep`: the column holds a `sep`-separated list, each element checked.
    `when`: only rows where `when[0] == when[1]` are checked.
    """

    column: str
    ref_table: str
    ref_column: str
    sep: str | None = None
    when: tuple[str, str] | None = None


@dataclass(frozen=True)
class RowRule:
    """A row-level rule, expressed as a polars SQL predicate that must hold for every row."""

    name: str
    predicate: str
    description: str
    severity: Severity = "error"


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Col, ...]
    primary_key: tuple[str, ...] = ()
    # Natural key not stated by ARCH; duplicates are reported as warnings only.
    natural_key: tuple[str, ...] = ()
    foreign_keys: tuple[FK, ...] = ()
    row_rules: tuple[RowRule, ...] = ()
    monday_columns: tuple[str, ...] = ()
    notes: str = ""
    required: bool = True
    column_names: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "column_names", tuple(c.name for c in self.columns))

    def col(self, name: str) -> Col:
        return next(c for c in self.columns if c.name == name)

    @property
    def filename(self) -> str:
        return f"{self.name}.csv"


REGIONS = Table(
    "regions",
    (
        Col("region_id", "str"),
        Col("region_name", "str"),
        Col("tier", "str", notes="e.g. metro / tier1 / tier2"),
        Col("cold_chain_available", "bool", notes="D2C deliverable if true"),
    ),
    primary_key=("region_id",),
)

SKUS = Table(
    "skus",
    (
        Col("sku_id", "str"),
        Col("product_line", "str", notes="e.g. cultivated_chicken, mycelium, seaweed, microbial"),
        Col("pack_size_kg", "float", ge=0),
        Col("status", "enum", enum=SKU_STATUS),
        Col("expected_launch_date", "date", nullable=True, notes="for rnd/pilot"),
        Col("shelf_life_days", "int", ge=0),
        Col("unit_cost_inr_per_kg", "float", ge=0, notes="production cost"),
        Col("list_price_d2c_inr_per_kg", "float", ge=0),
    ),
    primary_key=("sku_id",),
    notes="Product matrix, part 1.",
)

PRODUCT_MATRIX = Table(
    "product_matrix",
    (
        Col("sku_id", "str"),
        Col("channel", "enum", enum=CHANNELS),
        Col("region_id", "str"),
        Col("eligible_from", "date"),
        Col("eligible_to", "date", nullable=True),
    ),
    natural_key=("sku_id", "channel", "region_id", "eligible_from"),
    foreign_keys=(FK("sku_id", "skus", "sku_id"), FK("region_id", "regions", "region_id")),
    row_rules=(
        RowRule(
            "eligible_window_ordered",
            "eligible_to IS NULL OR eligible_to >= eligible_from",
            "eligible_to ≥ eligible_from",
        ),
    ),
    notes="Product matrix, part 2: SKU × channel × region eligibility.",
)

ORDERS = Table(
    "orders",
    (
        Col("order_id", "str"),
        Col("order_date", "date", notes="when requested"),
        Col("channel", "enum", enum=CHANNELS),
        Col("region_id", "str"),
        Col("sku_id", "str"),
        Col("customer_id", "str", nullable=True, notes="D2C only"),
        Col("account_id", "str", nullable=True, notes="B2B only"),
        Col("requested_qty_kg", "float", ge=0, notes="**unconstrained demand signal**"),
        Col("fulfilled_qty_kg", "float", ge=0, notes="≤ requested"),
        Col("unit_price_inr", "float", ge=0),
        Col("status", "enum", enum=ORDER_STATUS),
        Col("promised_date", "date"),
        Col("delivered_date", "date", nullable=True),
    ),
    primary_key=("order_id",),
    foreign_keys=(
        FK("region_id", "regions", "region_id"),
        FK("sku_id", "skus", "sku_id"),
        FK("customer_id", "customers", "customer_id", when=("channel", "D2C")),
        FK("account_id", "b2b_accounts", "account_id", when=("channel", "B2B")),
    ),
    row_rules=(
        RowRule(
            "fulfilled_le_requested",
            "fulfilled_qty_kg <= requested_qty_kg + 0.000001",
            "fulfilled_qty_kg ≤ requested_qty_kg",
        ),
        RowRule(
            "d2c_has_customer",
            "channel <> 'D2C' OR (customer_id IS NOT NULL AND account_id IS NULL)",
            "D2C orders carry customer_id and no account_id",
        ),
        RowRule(
            "b2b_has_account",
            "channel <> 'B2B' OR (account_id IS NOT NULL AND customer_id IS NULL)",
            "B2B orders carry account_id and no customer_id",
        ),
    ),
)

CAPACITY_BATCHES = Table(
    "capacity_batches",
    (
        Col("batch_id", "str"),
        Col("start_date", "date"),
        Col("end_date", "date"),
        Col("facility_id", "str"),
        Col("product_line", "str"),
        Col("planned_yield_kg", "float", ge=0),
        Col("actual_yield_kg", "float", ge=0, notes="0 if failed"),
        Col("outcome", "enum", enum=BATCH_OUTCOME),
    ),
    primary_key=("batch_id",),
    row_rules=(
        RowRule("batch_dates_ordered", "end_date >= start_date", "end_date ≥ start_date"),
        RowRule(
            "failed_has_zero_yield",
            "outcome <> 'failed' OR actual_yield_kg = 0",
            "failed batches have actual_yield_kg = 0",
        ),
    ),
    notes="In-house production batches.",
)

CAPACITY_PLAN = Table(
    "capacity_plan",
    (
        Col("week_start", "date", notes="covers history + ≥ 13 weeks ahead"),
        Col("facility_id", "str"),
        Col("product_line", "str"),
        Col("planned_batches", "int", ge=0),
        Col("planned_yield_per_batch_kg", "float", ge=0),
    ),
    natural_key=("week_start", "facility_id", "product_line"),
    monday_columns=("week_start",),
    notes="Forward-looking plan, known to the company.",
)

COMAN_CONTRACTS = Table(
    "coman_contracts",
    (
        Col("coman_id", "str"),
        Col("product_line", "str"),
        Col("available_from", "date", notes="earliest possible"),
        Col("lead_time_weeks", "int", ge=0, notes="activation → first output"),
        Col("min_commit_kg_per_week", "float", ge=0, notes="once active"),
        Col("max_kg_per_week", "float", ge=0),
        Col("unit_cost_inr_per_kg", "float", ge=0),
        Col("min_active_weeks", "int", ge=0),
    ),
    primary_key=("coman_id",),
    row_rules=(
        RowRule(
            "coman_min_le_max",
            "min_commit_kg_per_week <= max_kg_per_week",
            "min_commit_kg_per_week ≤ max_kg_per_week",
        ),
    ),
)

COMAN_ACTIVITY = Table(
    "coman_activity",
    (
        Col("coman_id", "str"),
        Col("week_start", "date"),
        Col("requested_kg", "float", ge=0),
        Col("delivered_kg", "float", ge=0, notes="reliability signal"),
    ),
    natural_key=("coman_id", "week_start"),
    foreign_keys=(FK("coman_id", "coman_contracts", "coman_id"),),
    monday_columns=("week_start",),
    notes="History of co-man use. May be empty (header only) if co-man was never used.",
)

B2B_ACCOUNTS = Table(
    "b2b_accounts",
    (
        Col("account_id", "str"),
        Col("account_type", "enum", enum=ACCOUNT_TYPES),
        Col("region_id", "str", notes="primary region"),
        Col("regions_served", "str", notes="pipe-separated region_ids (reach)"),
        Col("outlets_count", "int", ge=0, notes="reach"),
        Col("status", "enum", enum=ACCOUNT_STATUS),
        Col("onboarded_date", "date", nullable=True),
        Col("contract_start", "date", nullable=True),
        Col("contract_end", "date", nullable=True),
        Col("committed_kg_per_month", "float", nullable=True, ge=0),
        Col("contract_price_inr_per_kg", "float", nullable=True, ge=0),
        Col("shortfall_penalty_inr_per_kg", "float", nullable=True, ge=0),
        Col("requested_start_date", "date", nullable=True, notes="pipeline only"),
        Col("requested_kg_per_month", "float", nullable=True, ge=0, notes="pipeline only"),
    ),
    primary_key=("account_id",),
    foreign_keys=(
        FK("region_id", "regions", "region_id"),
        FK("regions_served", "regions", "region_id", sep="|"),
    ),
    row_rules=(
        RowRule(
            "contract_window_ordered",
            "contract_end IS NULL OR contract_start IS NULL OR contract_end >= contract_start",
            "contract_end ≥ contract_start",
        ),
    ),
)

MARKETING_DAILY = Table(
    "marketing_daily",
    (
        Col("date", "date"),
        Col("region_id", "str"),
        Col("channel", "enum", enum=CHANNELS, notes="usually D2C; B2B for trade marketing"),
        Col("campaign_type", "enum", enum=CAMPAIGN_TYPES),
        Col("campaign_id", "str"),
        Col("spend_inr", "float", ge=0),
        Col("impressions", "int", ge=0),
        Col("clicks", "int", ge=0),
        Col("unique_visitors", "int", ge=0),
        Col("bounces", "int", ge=0),
        Col("leads", "int", ge=0),
        Col("qualified_leads", "int", ge=0),
        Col("conversions", "int", ge=0, notes="new customers"),
        Col(
            "attributed_revenue_inr",
            "float",
            ge=0,
            notes="platform-attributed (biased; don't trust blindly)",
        ),
    ),
    natural_key=("date", "region_id", "channel", "campaign_id"),
    foreign_keys=(FK("region_id", "regions", "region_id"),),
    row_rules=(
        RowRule(
            "bounces_le_visitors",
            "bounces <= unique_visitors",
            "bounces ≤ unique_visitors",
            "warning",
        ),
        RowRule(
            "qualified_le_leads",
            "qualified_leads <= leads",
            "qualified_leads ≤ leads",
            "warning",
        ),
    ),
)

MARKETING_PLAN = Table(
    "marketing_plan",
    (
        Col("week_start", "date"),
        Col("region_id", "str"),
        Col("channel", "enum", enum=CHANNELS),
        Col("campaign_type", "enum", enum=CAMPAIGN_TYPES),
        Col("planned_spend_inr", "float", ge=0),
    ),
    natural_key=("week_start", "region_id", "channel", "campaign_type"),
    foreign_keys=(FK("region_id", "regions", "region_id"),),
    monday_columns=("week_start",),
    notes="Forward-looking planned spend.",
)

CUSTOMERS = Table(
    "customers",
    (
        Col("customer_id", "str"),
        Col("region_id", "str"),
        Col("acquired_date", "date"),
        Col(
            "acquisition_campaign_type",
            "enum",
            nullable=True,
            enum=CAMPAIGN_TYPES,
            notes="null = organic / word of mouth",
        ),
        Col("persona_segment", "str"),
        Col("churned_date", "date", nullable=True, notes="inferred by company rules"),
        Col("is_subscriber", "bool"),
    ),
    primary_key=("customer_id",),
    foreign_keys=(FK("region_id", "regions", "region_id"),),
    row_rules=(
        RowRule(
            "churn_after_acquired",
            "churned_date IS NULL OR churned_date >= acquired_date",
            "churned_date ≥ acquired_date",
        ),
    ),
    notes="D2C CRM.",
)

NPS_RESPONSES = Table(
    "nps_responses",
    (
        Col("response_date", "date"),
        Col("channel", "enum", enum=CHANNELS),
        Col("region_id", "str"),
        Col("customer_or_account_id", "str"),
        Col("score", "int", ge=0, le=10),
    ),
    foreign_keys=(
        FK("region_id", "regions", "region_id"),
        FK("customer_or_account_id", "customers", "customer_id", when=("channel", "D2C")),
        FK("customer_or_account_id", "b2b_accounts", "account_id", when=("channel", "B2B")),
    ),
)

TABLES: tuple[Table, ...] = (
    REGIONS,
    SKUS,
    PRODUCT_MATRIX,
    ORDERS,
    CAPACITY_BATCHES,
    CAPACITY_PLAN,
    COMAN_CONTRACTS,
    COMAN_ACTIVITY,
    B2B_ACCOUNTS,
    MARKETING_DAILY,
    MARKETING_PLAN,
    CUSTOMERS,
    NPS_RESPONSES,
)
TABLES_BY_NAME: dict[str, Table] = {t.name: t for t in TABLES}

# manifest.json: deliberately closed (additionalProperties=false) so that no field can
# describe the world's regime (ARCH §3, IDEATION §11).
MANIFEST_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "manifest.schema.json",
    "title": "manifest.json",
    "description": "Per-world manifest. Must not describe the world's regime.",
    "type": "object",
    "additionalProperties": False,
    "required": ["world_id", "contract_version", "history_start", "history_end", "generated_at"],
    "properties": {
        "world_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"},
        "contract_version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "history_start": {"type": "string", "format": "date", "description": "first history day"},
        "history_end": {"type": "string", "format": "date", "description": "last history day"},
        "plan_end": {
            "type": "string",
            "format": "date",
            "description": "last day covered by forward-looking plans",
        },
        "generated_at": {"type": "string", "format": "date-time"},
        "files": {
            "type": "object",
            "description": "Optional map of file name → SHA-256 hex digest.",
            "additionalProperties": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    },
}

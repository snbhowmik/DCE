"""Generate data/fixtures/tiny_world/. UNIT-TEST FIXTURE ONLY: NOT FOR EVALUATION.

A deliberately simple, hand-parameterized toy that exercises code paths (stockout, waitlist,
failed batch, co-man use, pipeline account). Its numbers mean nothing; never report accuracy or
business results on it (TASK rule 6).

Run from the repo root:  uv run python data/fixtures/make_tiny_world.py
"""

from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "tiny_world"
START = date(2024, 1, 1)  # Monday
N_WEEKS = 110
PLAN_WEEKS = N_WEEKS + 13
SKU = "SKU_CC_500"
LINE = "cultivated_chicken"
REGIONS = ("R_N", "R_S")
STOCKOUT_WEEK = 50
WAITLIST_WEEK = 51
FAILED_BATCH_WEEK = 40
COMAN_WEEKS = range(60, 68)

rng = np.random.default_rng(7)


def week(i: int) -> date:
    return START + timedelta(weeks=i)


def iso(d: date | None) -> str:
    return "" if d is None else d.isoformat()


def write(name: str, rows: list[dict[str, object]], header: list[str]) -> None:
    with (OUT / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    write(
        "regions",
        [
            {
                "region_id": "R_N",
                "region_name": "North",
                "tier": "metro",
                "cold_chain_available": "true",
            },
            {
                "region_id": "R_S",
                "region_name": "South",
                "tier": "tier1",
                "cold_chain_available": "true",
            },
        ],
        ["region_id", "region_name", "tier", "cold_chain_available"],
    )
    write(
        "skus",
        [
            {
                "sku_id": SKU,
                "product_line": LINE,
                "pack_size_kg": 0.5,
                "status": "production",
                "expected_launch_date": "",
                "shelf_life_days": 10,
                "unit_cost_inr_per_kg": 900,
                "list_price_d2c_inr_per_kg": 1600,
            }
        ],
        [
            "sku_id",
            "product_line",
            "pack_size_kg",
            "status",
            "expected_launch_date",
            "shelf_life_days",
            "unit_cost_inr_per_kg",
            "list_price_d2c_inr_per_kg",
        ],
    )
    write(
        "product_matrix",
        [
            {
                "sku_id": SKU,
                "channel": ch,
                "region_id": r,
                "eligible_from": iso(START),
                "eligible_to": "",
            }
            for ch in ("D2C", "B2B")
            for r in REGIONS
        ],
        ["sku_id", "channel", "region_id", "eligible_from", "eligible_to"],
    )

    accounts = [
        {
            "account_id": "ACC_DIST",
            "account_type": "distributor",
            "region_id": "R_N",
            "regions_served": "R_N|R_S",
            "outlets_count": 40,
            "status": "active",
            "onboarded_date": "2023-12-01",
            "contract_start": iso(START),
            "contract_end": "2026-12-31",
            "committed_kg_per_month": 400,
            "contract_price_inr_per_kg": 1250,
            "shortfall_penalty_inr_per_kg": 200,
            "requested_start_date": "",
            "requested_kg_per_month": "",
        },
        {
            "account_id": "ACC_REST",
            "account_type": "restaurant",
            "region_id": "R_S",
            "regions_served": "R_S",
            "outlets_count": 1,
            "status": "active",
            "onboarded_date": "2024-03-01",
            "contract_start": "2024-03-04",
            "contract_end": "2026-06-30",
            "committed_kg_per_month": 80,
            "contract_price_inr_per_kg": 1400,
            "shortfall_penalty_inr_per_kg": 100,
            "requested_start_date": "",
            "requested_kg_per_month": "",
        },
        {
            "account_id": "ACC_QSR",
            "account_type": "qsr_chain",
            "region_id": "R_N",
            "regions_served": "R_N|R_S",
            "outlets_count": 25,
            "status": "pipeline",
            "onboarded_date": "",
            "contract_start": "",
            "contract_end": "",
            "committed_kg_per_month": "",
            "contract_price_inr_per_kg": "",
            "shortfall_penalty_inr_per_kg": "",
            "requested_start_date": iso(week(N_WEEKS + 6)),
            "requested_kg_per_month": 300,
        },
    ]
    write("b2b_accounts", accounts, list(accounts[0].keys()))

    write(
        "coman_contracts",
        [
            {
                "coman_id": "CM_A",
                "product_line": LINE,
                "available_from": "2024-06-03",
                "lead_time_weeks": 4,
                "min_commit_kg_per_week": 50,
                "max_kg_per_week": 200,
                "unit_cost_inr_per_kg": 1200,
                "min_active_weeks": 4,
            },
            {
                "coman_id": "CM_B",
                "product_line": LINE,
                "available_from": iso(week(N_WEEKS + 4)),
                "lead_time_weeks": 6,
                "min_commit_kg_per_week": 100,
                "max_kg_per_week": 300,
                "unit_cost_inr_per_kg": 1150,
                "min_active_weeks": 8,
            },
        ],
        [
            "coman_id",
            "product_line",
            "available_from",
            "lead_time_weeks",
            "min_commit_kg_per_week",
            "max_kg_per_week",
            "unit_cost_inr_per_kg",
            "min_active_weeks",
        ],
    )
    write(
        "coman_activity",
        [
            {
                "coman_id": "CM_A",
                "week_start": iso(week(w)),
                "requested_kg": 100,
                "delivered_kg": round(float(rng.uniform(85, 100)), 1),
            }
            for w in COMAN_WEEKS
        ],
        ["coman_id", "week_start", "requested_kg", "delivered_kg"],
    )

    # In-house: one 300 kg batch per week, two-week cycle; one failed batch.
    batches = []
    for w in range(N_WEEKS):
        failed = w == FAILED_BATCH_WEEK
        ratio = float(rng.uniform(0.85, 1.0))
        outcome = "failed" if failed else ("partial" if ratio < 0.88 else "success")
        batches.append(
            {
                "batch_id": f"B{w:04d}",
                "start_date": iso(week(w)),
                "end_date": iso(week(w) + timedelta(days=13)),
                "facility_id": "F1",
                "product_line": LINE,
                "planned_yield_kg": 300,
                "actual_yield_kg": 0 if failed else round(300 * ratio, 1),
                "outcome": outcome,
            }
        )
    write("capacity_batches", batches, list(batches[0].keys()))
    write(
        "capacity_plan",
        [
            {
                "week_start": iso(week(w)),
                "facility_id": "F1",
                "product_line": LINE,
                "planned_batches": 1,
                "planned_yield_per_batch_kg": 300,
            }
            for w in range(PLAN_WEEKS)
        ],
        [
            "week_start",
            "facility_id",
            "product_line",
            "planned_batches",
            "planned_yield_per_batch_kg",
        ],
    )

    # Marketing: one ppc campaign per region, daily; spend steps up in weeks 30-45 in R_N.
    base_spend = {"R_N": 3000.0, "R_S": 1500.0}
    weekly_spend: dict[tuple[str, int], float] = {}
    mkt = []
    for w in range(N_WEEKS):
        for r in REGIONS:
            mult = 1.8 if (r == "R_N" and 30 <= w < 46) else 1.0
            for d in range(7):
                spend = round(base_spend[r] * mult * float(rng.uniform(0.9, 1.1)), 2)
                visitors = int(spend / 12)
                clicks = int(visitors * 1.2)
                leads = int(visitors * 0.08)
                conv = int(leads * 0.25)
                mkt.append(
                    {
                        "date": iso(week(w) + timedelta(days=d)),
                        "region_id": r,
                        "channel": "D2C",
                        "campaign_type": "ppc",
                        "campaign_id": f"PPC_{r}",
                        "spend_inr": spend,
                        "impressions": clicks * 30,
                        "clicks": clicks,
                        "unique_visitors": visitors,
                        "bounces": int(visitors * 0.45),
                        "leads": leads,
                        "qualified_leads": int(leads * 0.6),
                        "conversions": conv,
                        "attributed_revenue_inr": conv * 2400,
                    }
                )
                weekly_spend[(r, w)] = weekly_spend.get((r, w), 0.0) + spend
    write("marketing_daily", mkt, list(mkt[0].keys()))
    write(
        "marketing_plan",
        [
            {
                "week_start": iso(week(w)),
                "region_id": r,
                "channel": "D2C",
                "campaign_type": "ppc",
                "planned_spend_inr": round(weekly_spend.get((r, w), base_spend[r] * 7), 2),
            }
            for w in range(PLAN_WEEKS)
            for r in REGIONS
        ],
        ["week_start", "region_id", "channel", "campaign_type", "planned_spend_inr"],
    )

    # Customers + D2C orders.
    customers: list[dict[str, object]] = []
    pool: dict[str, list[str]] = {r: [] for r in REGIONS}
    orders = []
    oid = 0
    for w in range(N_WEEKS):
        for r in REGIONS:
            new = 3 + int(weekly_spend[(r, w)] / 4000)
            for _ in range(new):
                cid = f"C{len(customers):05d}"
                acq = week(w) + timedelta(days=int(rng.integers(0, 7)))
                churn = acq + timedelta(weeks=int(rng.integers(8, 60)))
                customers.append(
                    {
                        "customer_id": cid,
                        "region_id": r,
                        "acquired_date": iso(acq),
                        "acquisition_campaign_type": "ppc" if rng.random() < 0.7 else "",
                        "persona_segment": "urban_foodie"
                        if rng.random() < 0.5
                        else "health_seeker",
                        "churned_date": iso(churn) if churn < week(N_WEEKS) else "",
                        "is_subscriber": "true" if rng.random() < 0.2 else "false",
                    }
                )
                pool[r].append(cid)
            season = 1.0 + 0.15 * np.sin(2 * np.pi * w / 52)
            n_orders = int((12 if r == "R_N" else 7) * season + len(pool[r]) * 0.05)
            for k in range(n_orders):
                cid = pool[r][int(rng.integers(0, len(pool[r])))]
                req = float(rng.choice([0.5, 1.0, 1.5, 2.0]))
                status, ful = "fulfilled", req
                if w == STOCKOUT_WEEK and r == "R_N":
                    status, ful = ("cancelled_stockout", 0.0) if k % 2 else ("partial", req / 2)
                if w == WAITLIST_WEEK and r == "R_N" and k < 3:
                    status, ful = "waitlisted", 0.0
                od = week(w) + timedelta(days=int(rng.integers(0, 7)))
                oid += 1
                orders.append(
                    {
                        "order_id": f"O{oid:06d}",
                        "order_date": iso(od),
                        "channel": "D2C",
                        "region_id": r,
                        "sku_id": SKU,
                        "customer_id": cid,
                        "account_id": "",
                        "requested_qty_kg": req,
                        "fulfilled_qty_kg": ful,
                        "unit_price_inr": 1600,
                        "status": status,
                        "promised_date": iso(od + timedelta(days=2)),
                        "delivered_date": iso(od + timedelta(days=2)) if ful > 0 else "",
                    }
                )
    write("customers", customers, list(customers[0].keys()))

    # B2B: one weekly order per active account, around commitment / 4.33.
    for acc in accounts[:2]:
        start = date.fromisoformat(str(acc["contract_start"]))
        weekly = float(acc["committed_kg_per_month"]) / 4.33  # type: ignore[arg-type]
        for w in range(N_WEEKS):
            if week(w) < start:
                continue
            req = round(weekly * float(rng.uniform(0.8, 1.15)), 1)
            ful = round(req * 0.6, 1) if w == STOCKOUT_WEEK else req
            oid += 1
            orders.append(
                {
                    "order_id": f"O{oid:06d}",
                    "order_date": iso(week(w)),
                    "channel": "B2B",
                    "region_id": acc["region_id"],
                    "sku_id": SKU,
                    "customer_id": "",
                    "account_id": acc["account_id"],
                    "requested_qty_kg": req,
                    "fulfilled_qty_kg": ful,
                    "unit_price_inr": acc["contract_price_inr_per_kg"],
                    "status": "partial" if ful < req else "fulfilled",
                    "promised_date": iso(week(w) + timedelta(days=3)),
                    "delivered_date": iso(week(w) + timedelta(days=3)),
                }
            )
    orders.sort(key=lambda o: (str(o["order_date"]), str(o["order_id"])))
    write("orders", orders, list(orders[0].keys()))

    nps = []
    for w in range(0, N_WEEKS, 2):
        for r in REGIONS:
            if not pool[r]:
                continue
            cid = pool[r][int(rng.integers(0, len(pool[r])))]
            score = 3 if w in (STOCKOUT_WEEK, STOCKOUT_WEEK + 2) else int(rng.integers(6, 11))
            nps.append(
                {
                    "response_date": iso(week(w) + timedelta(days=3)),
                    "channel": "D2C",
                    "region_id": r,
                    "customer_or_account_id": cid,
                    "score": score,
                }
            )
        if w % 8 == 0:
            nps.append(
                {
                    "response_date": iso(week(w) + timedelta(days=4)),
                    "channel": "B2B",
                    "region_id": "R_N",
                    "customer_or_account_id": "ACC_DIST",
                    "score": int(rng.integers(6, 10)),
                }
            )
    write(
        "nps_responses",
        nps,
        ["response_date", "channel", "region_id", "customer_or_account_id", "score"],
    )

    (OUT / "manifest.json").write_text(
        json.dumps(
            {
                "world_id": "tiny_world",
                "contract_version": "1.0.0",
                "start_date": iso(START),
                "end_date": iso(week(N_WEEKS) - timedelta(days=1)),
                "generated_at": "2026-09-29T00:00:00Z",
            },
            indent=2,
        )
        + "\n"
    )
    (OUT / "NOT_FOR_EVALUATION").write_text(
        "Unit-test fixture generated by data/fixtures/make_tiny_world.py.\n"
        "Never report accuracy or business results on this data (TASK rule 6).\n"
    )


if __name__ == "__main__":
    main()

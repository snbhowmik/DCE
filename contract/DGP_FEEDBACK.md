# Feedback for the DGP side: first world set (2026-09-29)

Contract-level observations from ingesting world_01 … world_06 and world_01_drop2. Written from
the app side using only the delivered CSVs; no DGP code was read. Please forward as-is.

## 1. Manifest field names: accepted on the app side (CC-002)

The drops use `history_start`, `history_end`, `plan_end`. The app now uses these names (closed
schema: `world_id`, `contract_version`, `history_start`, `history_end`, `generated_at`, optional
`plan_end`, `files`). **No change needed.** All seven drops pass contract validation with 0 errors.

## 2. `b2b_accounts.contract_end` for active accounts (CC-003, please confirm)

Every `active` account has `contract_end` on the day after `history_end` (e.g. 2024-12-30 for a
2024-12-29 snapshot). In world_01_drop2 (history to 2025-03-30) the same accounts still show
2024-12-30, remain `active`, and keep ordering at the same rate after that date.

**Request:** `contract_end` should be the real forward end date of the agreement, or empty for
evergreen / auto-renewing contracts. Until then the app treats an active contract ending at the
snapshot boundary as a rolling renewal (a later end date is honored).

## 3. Realized marketing spend stops early in every world (DQ-001, please fix)

In every drop, D2C `marketing_daily.spend_inr` is zero for all rows after an early week
(world_01: last spend in the week of 2022-08-01; 125 of 156 history weeks have zero spend), and
visitors, leads and conversions are zero from then on. Meanwhile `marketing_plan` shows positive
planned spend for every history and forward week, and D2C orders continue throughout.

| drop | weeks with realized D2C spend | last week with spend |
|---|---|---|
| world_01 | 31 / 156 | 2022-08-01 |
| world_01_drop2 | 31 / 169 | 2022-08-01 |
| world_02 | 22 / 156 | 2022-05-30 |
| world_03 | 56 / 156 | 2023-01-23 |
| world_04 | 38 / 156 | 2022-09-19 |
| world_05 | 29 / 182 | 2022-07-18 |
| world_06 | 36 / 156 | 2022-09-05 |

This looks like a budget-reallocation loop collapsing budgets to zero. Effects on the app: the
spend-response model cannot be identified (every region flagged low-confidence), CAC / ROI /
funnel metrics are undefined for most of history, and planned spend no longer describes what
happened.

**Request:** regenerate with realized spend present throughout history (planned and realized
spend should broadly agree, with realistic deviations).

## 4. Pilot SKUs with orders (informational)

world_02 and world_05 contain a small number of D2C orders for `pilot` SKUs of other product
lines. The app plans one commercial line (production status) and reports other-line demand as
out-of-scope. No change needed unless those lines are meant to compete for the same capacity.

## 5. Future handoffs: share only `out/`

The handoff summary for this set included the sealed world table (configs and regime
descriptions per world). The app side had to log it as an integrity event, and these worlds
can't back a blind evaluation. For the next set, please send only the `out/<world_id>/` drops;
keep configs, seeds and descriptions sealed until the app's evaluation is frozen.

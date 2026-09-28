# NOTES — Biokraft Demand-Capacity Engine (DCE)

The project's memory. **Append-only**: never edit or delete past entries. If something was wrong, add a new entry that supersedes it and reference the old ID.

Every task completion, design decision, assumption, contract change, integrity event, and evaluation result is logged here.

---

## 0. How to log

### 0.1 Task Log entry (after every TASK.md item)
```markdown
### TL-<nnn> · <task id> · <YYYY-MM-DD>
- **Agent/author:** <e.g. Claude Code / Subir>
- **Summary:** what was built, in 1–3 lines
- **Files touched:** key paths
- **Tests:** added / passing (counts)
- **Decisions made:** D-xxx (or "none")
- **Deviations from ARCH:** none | description + D-xxx
- **Known issues / follow-ups:** …
```

### 0.2 Decision record (ADR-lite)
```markdown
### D-<nnn> · <title> · <YYYY-MM-DD> · status: proposed | accepted | superseded by D-yyy
- **Context:** why a decision was needed
- **Options considered:** A / B / C
- **Decision:** chosen option
- **Consequences:** tradeoffs, what becomes harder
- **Refs:** IDEATION/ARCH sections, task IDs
```

### 0.3 Assumption
```markdown
### A-<nnn> · <statement> · status: open | validated | invalidated
- **Why assumed:** …
- **Impact if wrong:** …
- **How to validate:** …
```

### 0.4 Integrity event
```markdown
### IE-<nnn> · <YYYY-MM-DD>
- **What happened:** e.g. saw DGP parameter file / world description
- **Worlds affected:** …
- **Action:** exclude world_xx from headline results / none needed (explain)
```

### 0.5 Contract change request
```markdown
### CC-<nnn> · <YYYY-MM-DD> · status: requested | accepted | rejected
- **Change:** field / file / semantics
- **Reason:** what the app needs and why (never "because the DGP does X")
- **Version bump:** 1.0.0 → 1.x.0
```

### 0.6 Evaluation log
```markdown
### EV-<nnn> · <YYYY-MM-DD> · FROZEN
- **Report path:** reports/eval_<ts>/
- **Report SHA-256:** …
- **Worlds:** world_01 … world_nn (blind)
- **Headline results:** vs. PRD §9 targets
- **Target revisions before this run:** D-xxx or none
```

### 0.7 Tuning note (config value changes)
```markdown
### TN-<nnn> · <config file:key> · <old> → <new> · <YYYY-MM-DD>
- **Reason:** …
- **Evidence:** backtest / stress result (training window only, never the holdout)
```

---

## 1. Decisions

### D-001 · Forecasting is descriptive; strategy lives only in allocation · 2026-09-29 · accepted
- **Context:** The quarter's focus (growth / stability / D2C expansion) must change recommendations. Letting it shape the forecast would make forecasts self-serving.
- **Options:** (A) mode-conditioned forecasts; (B) neutral forecast + mode-conditioned optimizer.
- **Decision:** B.
- **Consequences:** mode-invariance test required (T2.8); the forecast module cannot import strategy config.
- **Refs:** IDEATION P1, ARCH §1, §9.4

### D-002 · DGP built by a separate AI (Gemini CLI) in a separate repo · 2026-09-29 · accepted
- **Context:** If the same author writes the data generator and the forecaster, any accuracy claim is circular.
- **Decision:** `biokraft-dgp` is built with Gemini CLI. This repo sees only the data contract and CSV drops.
- **Consequences:** contract changes go through CC entries; integrity events must be logged.
- **Refs:** IDEATION P2, §11; ARCH §3

### D-003 · Blind worlds + freeze-before-unblind evaluation · 2026-09-29 · accepted
- **Context:** Knowing a world's regime biases tuning.
- **Decision:** worlds are delivered as `world_01…`; results are frozen (EV entry with hash) before regime descriptions are requested.
- **Refs:** IDEATION §11, TASK T11.3–T11.4

### D-004 · Probabilistic forecasts with conformal calibration and sample paths · 2026-09-29 · accepted
- **Context:** Both demand and capacity are uncertain; point forecasts hide risk.
- **Decision:** P10/P50/P90 + sample paths; split-conformal calibration.
- **Refs:** IDEATION P3, ARCH §5.4

### D-005 · Forecast unconstrained demand, not sales · 2026-09-29 · accepted
- **Context:** In a capacity-constrained business, sales are censored by stockouts. Forecasting sales would underestimate demand and hide shortfalls.
- **Decision:** demand = requested qty (incl. waitlisted, stockout-cancelled); sales kept for reporting.
- **Refs:** ARCH §3.4, §5.2

### D-006 · Allocation + spend solved as one LP/MILP (PuLP + HiGHS) · 2026-09-29 · accepted
- **Context:** Allocation (D1) and marketing geography (D2) are coupled through capacity.
- **Decision:** spend is a decision variable with piecewise-linear concave response segments, so it stays an LP, plus co-man binaries → MILP.
- **Refs:** IDEATION §2, §5.3; ARCH §5.7

### D-007 · The LLM never produces numbers · 2026-09-29 · accepted
- **Decision:** the LLM only explains payloads and parses scenarios into validated specs; the number-grounding validator is enforced with a template fallback.
- **Refs:** IDEATION P4, ARCH §5.11

### D-008 · Evidence-gated D2C expansion with a capped exploration budget · 2026-09-29 · accepted
- **Context:** Riskier D2C expansion should only happen where history shows *sustained* positive response; unproven regions still need a way to generate evidence.
- **Decision:** RES gate per mode + exploration pool (share of budget), intended to run as geo-experiments.
- **Refs:** IDEATION §8, §9.1; ARCH §5.7 constraint 7

### D-009 · Mitigations from ops/revenue-management practice, not invented company history · 2026-09-29 · accepted
- **Context:** Biokraft has no usable record of past shortage handling; fabricating one would be dishonest.
- **Decision:** catalog M1–M6 with lead-time feasibility and re-solve-based impact ranking.
- **Refs:** IDEATION §10, ARCH §5.9

### D-010 · Granularity: weekly forecast, monthly allocation, 13-week horizon · 2026-09-29 · accepted
- **Context:** The problem statement speaks of monthly output; breach timing needs weekly resolution; the quarter is the strategy cycle.
- **Refs:** ARCH §5.4, §5.7

### D-011 · Surplus is flagged as a risk (perishability) · 2026-09-29 · accepted
- **Refs:** IDEATION §5.2, §10; ARCH §5.8

### D-012 · Repo tooling layout · 2026-09-29 · accepted
- **Context:** README links to `docs/*.md` but the docs were at repo root; README runs `uv run dce ...` from the root while ARCH puts `pyproject.toml` in `backend/`.
- **Options:** (A) keep docs at root and fix links; (B) move docs into `docs/` per ARCH §4. For uv: (A) run everything with `--directory backend`; (B) virtual uv workspace at the root with `backend` as the only member.
- **Decision:** docs moved to `docs/`; root `pyproject.toml` is a virtual uv workspace so `uv run dce` works from the root. Python pinned to 3.12 (wheels for statsforecast/lightgbm/highspy). Tests live in `backend/dce/tests/` (ARCH §4). `dce.paths` resolves the repo root independent of cwd (override: `$DCE_ROOT`).
- **Consequences:** `make` targets are the canonical entry points. The frontend template adds `frontend/AGENTS.md` (Next.js 16 notes); kept.
- **Refs:** ARCH §2, §4; T0.1

### D-013 · Import-linter guards strategy independence from T0.1 · 2026-09-29 · accepted
- **Context:** P1 requires forecast/capacity/response/metrics to be strategy-agnostic. T2.8 asks for an import-linter rule; enforcing it from day one is cheaper than retrofitting.
- **Decision:** `.importlinter` forbids `dce.{forecast,capacity,response,metrics,demand}` from importing `dce.{optimize,risk,mitigate,onboarding,ai}`; `make lint` runs it. T2.8 adds the config-file read check.
- **Refs:** IDEATION P1; ARCH §1, §9.4; T2.8

### D-014 · Contract spec lives in Python; JSON Schemas and README are generated · 2026-09-29 · accepted
- **Context:** ARCH §4 lists `contract/models.py (pandera)`. `contract/` is not a Python package, and hand-maintaining 13 JSON Schemas + pandera models + README in parallel invites drift.
- **Options:** (A) hand-written JSON Schemas as source, pandera parsed from them; (B) `contract/models.py` loaded via importlib; (C) Python spec in `dce.contract.spec` → generated `contract/schemas/*.json`, `contract/README.md`, `contract/CONTRACT_VERSION`; pandera built from the spec at runtime.
- **Decision:** C. `dce contract export` regenerates; a test fails if the committed files drift from the spec. `contract/` stays language-neutral (what the DGP side reads).
- **Consequences:** deviation from ARCH §4 file location only; content is ARCH §3 verbatim plus the clarifications in CC-001.
- **Refs:** ARCH §3, §4; T0.2

### D-015 · Validation reads CSVs as strings and casts per contract · 2026-09-29 · accepted
- **Context:** Type errors must be reported with row locations; letting the CSV reader infer types hides them.
- **Decision:** read all columns as strings; cast per contract; any non-empty cell that fails to cast is a `type` error with CSV line numbers. Pandera (polars backend) then checks nulls, enums, ranges, and PK uniqueness. FKs, row rules, and Monday checks run afterwards. Severity: `error` blocks ingest; `warning` is reported only.
- **Refs:** ARCH §5.1; T0.2, T1.1

### D-016 · Provenance formats · 2026-09-29 · accepted
- **Context:** ARCH §5.12 names the fields but not their formats.
- **Decision:** `run_id = run_<UTC yyyymmddThhmmss>_<8 hex>` (sortable; unique per execution; never an input to any computation). `dataset_hash` = SHA-256 over sorted `filename:sha256` lines of contract files only (13 CSVs + manifest), so READMEs/markers don't change it and file swaps do. `config_hash` = SHA-256 of canonical JSON (sorted keys). `git_sha` gets a `-dirty` suffix when tracked files differ from HEAD; `unknown` outside git. Sub-seeds = first 63 bits of SHA-256(`seed|module|series…`). `runs` also stores the full effective config (JSON) plus `kind` and `parent_run_id` (for scenarios/onboarding re-solves).
- **Refs:** ARCH §5.12, §8; T0.4

### D-017 · Ingest severities and failure behavior · 2026-09-29 · accepted
- **Context:** FR-1 lists checks without severities.
- **Decision:** errors (block use): contract violations, history < 104 weeks, `capacity_plan` not covering every week from history start to 13 weeks past history end. Warnings: rows outside the manifest window, weeks with no orders inside a channel×region's active span, days without marketing rows, `marketing_plan` ending before the 13-week horizon, active accounts without a commitment. A failing dataset still gets `validation_report.{json,md}` and a `datasets` row (with `n_errors`), but no Parquet, so no downstream module can load it. The path guard resolves symlinks and requires a path strictly inside `data/incoming/` or `data/fixtures/` (the roots themselves are refused).
- **Refs:** ARCH §3, §5.1, §9.5; PRD FR-1, FR-3; T1.1

### D-018 · `cancelled_other` orders are excluded from demand · 2026-09-29 · accepted
- **Context:** ARCH §5.2 says demand = Σ `requested_qty_kg`; D-005 lists only waitlisted and stockout-cancelled orders as additions to sales. `cancelled_other` is customer-initiated and never needed capacity; counting it would inflate forecasts by the cancellation rate and overstate shortfall risk.
- **Options:** (A) all statuses (ARCH literal); (B) exclude `cancelled_other`, report it separately.
- **Decision:** B. `demand_kg` excludes `cancelled_other` (kept as `cancelled_other_kg`). `is_censored` follows ARCH (waitlisted + cancelled_stockout share > 0). Partial fills are reported as `partial_short_kg`/`unmet_kg` but do not set `is_censored`, because the full requested quantity is still observed. Series are zero-filled from their first order week to the last history week; D2C series drop `customer_id`, B2B series are keyed by account × SKU × region.
- **Consequences:** deviation from ARCH §5.2 wording; consistent with D-005 intent.
- **Refs:** ARCH §5.2; D-005; T1.2

### D-019 · Funnel metric definitions where IDEATION §7.2 leaves room · 2026-09-29 · accepted
- **Context:** Several §7.2 formulas need a concrete data mapping.
- **Decision:**
  - *New customers* come from the CRM (`customers.acquired_date`; B2B: `b2b_accounts.onboarded_date`), not platform `conversions`. `cac` = spend ÷ all new customers (blended); `cac_paid` = spend ÷ customers with a non-null acquisition campaign. Attributable sales cost is not in the contract → 0.
  - *Churn* = lost ÷ at-start, where at-start = acquired before the period and not churned before it; customers acquired and churned in the same period count as new but not as lost. Computed by cumulative counts (scales with customers, not customers × periods). B2B churn uses `contract_end` of `churned` accounts.
  - *Period LTV* (D2C) is the §7.2 formula with the period's AOV, frequency (orders ÷ active customers), margin %, and 1/churn; null when churn = 0. B2B LTV = monthly contract margin × contract tenure (mean over live contracts; null for open-ended contracts). *Cohort LTV* (region × acquisition month; realized + projected with capped lifetime) is the robust estimate used by RES.
  - *MRR* only at month grain (B2B contract value + D2C subscriber revenue); null at week grain.
  - *ROI* = (attributed revenue × realized margin % − spend) ÷ spend; platform attribution is biased (A-008), so ROI is a scorecard only.
  - *Unique visitors* are period sums of daily uniques (the contract has no visitor ids).
  - *Organic share* = share of new customers with no acquisition campaign (proxy for word of mouth until T4).
  - Every ratio returns null (never inf/NaN) on zero or null denominators.
- **Refs:** IDEATION §7.2; ARCH §5.3; PRD FR-4; T1.3

### D-020 · RES and AQS operational definitions · 2026-09-29 · accepted
- **Context:** ARCH §5.3 gives the RES/AQS structure; several quantities need concrete definitions.
- **Decision (RES, per region × channel):**
  - *Sustained lift:* a step-up at week t is weekly spend ≥ (1 + 20%) × mean of the 4 prior weeks for ≥ 2 consecutive weeks (pre-period mean rather than the single prior week, so ±10% day-to-day noise doesn't trigger). Early lift = mean demand in weeks t+1…t+4 − pre mean; late = t+5…t+10 − pre mean; ratio = late ÷ early, clipped to [0, 1.5]; events with early ≤ 0 are uninformative (no ratio). Events don't overlap. Component = mean ratio.
  - *Econ:* window LTV ÷ window CAC; D2C LTV = cohort region LTV, B2B = contract LTV.
  - *Retention:* mean of (1 − window monthly churn) and repeat-purchase rate (D2C customers with ≥ 2 served orders in the window).
  - *NPS* over the trailing window (52 weeks, config).
  - z-scores within channel across regions; a missing component gets z = 0 and is listed in `missing_components`. Shrinkage: `conf = n_eff/(n_eff + k)`, `n_eff` = window new customers (D2C) or active accounts (B2B), `k` per channel in config; `RES = conf·RES_raw + (1−conf)·mean_channel(RES_raw)`. `confidence` is reported.
- **Decision (AQS, per account):** volume = commitment (pipeline: requested volume); stability = 1 − CV of monthly ordered/committed; reach = ln(1 + outlets) × regions served; margin = contract price − production unit cost; reliability = share of contract-active weeks with an order; penalty exposure = penalty/kg × commitment (subtracted); concentration = volume ÷ planned monthly in-house capacity (last 13 weeks), penalized as `w·share/cap` and flagged above `concentration_cap`. z against accounts with contract history. Pipeline accounts: stability/reliability/margin/penalty from account-type means (global mean if the type is absent), `prior=true`, `confidence=0`. Weight profiles (`balanced`, `reach_heavy`, `reliability_heavy`) are passed in by name; the metrics module never reads strategy modes (P1).
- **Consequences:** fill-history (our fill rate to the account) is not in AQS: it measures us, not the account. Payment reliability isn't in the contract.
- **Refs:** IDEATION §9; ARCH §5.3; PRD FR-5; T1.4

### D-021 · Backtest conventions · 2026-09-29 · accepted
- **Context:** ARCH §5.4 fixes folds/horizon/step/metrics but not boundaries and scaling.
- **Decision:** a fold's `origin` is its first forecast week; models receive only rows with `week_start < origin` (plus a separate known-future covariate frame). The newest fold's horizon ends at the last history week; older folds step back 4 weeks; folds leaving < 52 training weeks are dropped (config). Models implement `fit_predict(history, horizon, future) → series_id, week_start, q10, q50, q90`; the harness rejects wrong shapes. MASE uses P50 with a per-fold scale from that fold's training data (m = 52, falling back to m = 1 if the series is too short or the seasonal scale is 0; null if both are 0). Pinball is the mean over q ∈ {0.1, 0.5, 0.9}, also reported scaled by the MASE scale. Coverage = share of actuals in [q10, q90].
- **Refs:** ARCH §5.4; PRD FR-7; T2.1

### D-022 · Baseline quantiles · 2026-09-29 · accepted
- **Context:** "quantiles from empirical residuals" could shift P50 by the median residual, which would no longer be the textbook seasonal-naive baseline that MASE and FR-8 fall back to.
- **Decision:** P50 = the rule's point forecast. P10/P90 = point + 10th/90th percentile of the rule's residuals on training history (SeasonalNaive: seasonal differences; WindowAverage and the naive fallback: h-step residuals per horizon step, pooled if < 8 residuals). Crossing guard keeps P50 anchored (P10 = min(P10, P50), P90 = max(P90, P50)); all quantiles clipped at 0. SeasonalNaive(52) falls back to last-value when history < 53 weeks.
- **Consequences:** biased rules (e.g. naive on a trend) get one-sided bands; calibration (T2.6) corrects coverage.
- **Refs:** ARCH §5.4; T2.2

### D-023 · statsforecast wrappers · 2026-09-29 · accepted
- **Context:** statsforecast ETS only supports seasonal periods ≤ 24; with weekly data (m = 52) AutoETS fits non-seasonal models. AutoTheta applies its own seasonality test.
- **Decision:** keep `AutoETS(season_length=52)` and `AutoTheta(season_length=52)` as specified; accept ETS as non-seasonal (weekly seasonality is carried by SeasonalNaive and LightGBM calendar features, and selection picks per series). P50 = model mean, P10/P90 = 80% interval, clipped ≥ 0 with P50-anchored crossing guard. `fallback_model=Naive()` for series the model can't fit; `n_jobs=1` for determinism. Series whose history stops before the origin are forecast through the gap and cut to the horizon.
- **Refs:** ARCH §5.4; T2.3

### D-024 · LightGBM formulation: direct multi-horizon, origin-normalized · 2026-09-29 · accepted
- **Context:** ARCH lists lags 1–4 with a 13-week horizon. Recursive forecasting would feed predictions back as lags; that compounds error and makes the leakage boundary harder to audit.
- **Options:** (A) recursive one-step model; (B) one model per h (39 models); (C) one direct model per quantile with `h` as a feature.
- **Decision:** C. A training row is (series, origin o, step h) → y[o+h]. History features (lags 1–4, 8, 13 counted back from o; seasonal y[o+h−52], only if ≤ o; rolling mean/std over 4 and 13 weeks) read weeks ≤ o only. Known-future features at the target week: sin/cos week-of-year, month, Indian holiday count + major-festival flag (`holidays` package), adstocked **planned** spend (θ = 0.5) and its ratio to the origin's, region tier, channel, list/contract price. Values are divided by the series' 13-week mean + 1 kg (`log_scale` kept as a feature) so one global model spans regions and accounts. Native `lgb.train` (no scikit-learn dependency), `deterministic`, single thread, derived seed. Rows sorted across quantiles, clipped at 0. Falls back to WindowAverage(8) below 200 training rows.
- **Consequences:** `dce.forecast.covariates.build_covariates` reads only `marketing_plan`, `regions`, `skus`, `b2b_accounts`, and the calendar (enforced by test). Realized price isn't a feature: future realized price is unknown at forecast time.
- **Refs:** ARCH §5.4; T2.4

### D-025 · Anomaly detection runs before model fitting, on D2C only · 2026-09-29 · accepted
- **Context:** ARCH §5.4 says "robust z-score on backtest residuals". But the models must train on winsorized data, so detection has to happen before any model is fit; using model residuals would be circular.
- **Decision:** residual = y − centered 9-week rolling median; robust z = 0.6745·(r − median r)/MAD per series (mean-absolute-deviation fallback when MAD = 0; no flags if both are 0). |z| > 3.5 → `is_anomaly`; training uses `y_clean` = baseline + residual clipped to ±3.5σ̂ (≥ 0). Negative flags in censored weeks are dropped (censoring explains them). Default scope is D2C (`config/scoring.yaml: anomaly.channels`): viral spikes are a D2C phenomenon, and lumpy B2B orders would otherwise be flagged constantly. Flags are reported via `anomaly_report`.
- **Consequences:** deviation from the ARCH wording (residual source); intent (flag, winsorize, never extrapolate) unchanged.
- **Refs:** IDEATION §7.2; ARCH §5.4; PRD FR-9; T2.5

### D-026 · Selection, calibration and path sampling details · 2026-09-29 · accepted
- **Context:** ARCH §5.4 specifies the methods; the operational details matter for honesty and for downstream consistency.
- **Decision:**
  - *Selection:* per series, the lowest mean pinball among models whose MASE is strictly below SeasonalNaive(52)'s; otherwise SeasonalNaive with reason "baseline fallback". If the baseline MASE is undefined (constant training history), lowest pinball wins. SeasonalNaive is always computed even if removed from config. Backtests, selection and calibration are scored on the anomaly-cleaned series (we don't try to predict spikes).
  - *Calibration:* one-sided CQR per side (α = 0.1): a = the ⌈(n+1)(1−α)⌉/n quantile of `q10 − y` (resp. `y − q90`) over the chosen model's backtest rows; per series when n ≥ 30, else pooled by (channel, tier). Adjustments can narrow as well as widen. P10 is clipped at 0.
  - *Reported coverage:* leave-one-fold-out cross-fit (calibrate on the other folds, measure on the held-out fold), so the number isn't tautologically 80%. True out-of-sample coverage is measured on the evaluator holdout (T11).
  - *Paths:* block bootstrap (block = 4 weeks) over the chosen model's fold residual trajectories, drawn at matching horizon steps; residuals median-centered; tails rescaled per step so path P10/P90 ≈ calibrated bands (paths and displayed bands agree, which the stress test and breach probabilities rely on). Seed = derive(seed, "forecast_paths", series_id). Clipped at 0.
  - *Artifact hash* = SHA-256 of the sorted quantile table (CSV, 10 dp) + series order + rounded path bytes; used for mode invariance (T2.8).
- **Refs:** ARCH §5.4; PRD FR-6, FR-8; D-004; T2.6

### D-027 · B2B forecast mechanics · 2026-09-29 · accepted
- **Context:** ARCH §5.4 says to model the order-to-commitment ratio per account, then multiply by commitment.
- **Decision:** included = `status == active` with a positive commitment, a contract start, and no contract end before the first horizon week; everything else is excluded with a stated reason (pipeline, churned, paused, no commitment, contract ended). Weekly commitment = monthly × 12/52. Ratio series run from max(contract start, history start) to the last history week at account level (summed over SKU/region), and go through the same selection/calibration/path pipeline as D2C (so they get the same model choice rules). kg = ratio × weekly commitment; weeks starting after `contract_end` are 0 in quantiles and paths. Accounts too young for any backtest fold get the baseline with pooled calibration; their paths are drawn from the calibrated band with a single persistent z per path (conservative: fully correlated across the horizon).
- **Refs:** ARCH §5.4; T2.7; A-010

### D-028 · How mode invariance is enforced · 2026-09-29 · accepted
- **Context:** T2.8 asks that the forecast module have "no import path to `config/strategy_modes.yaml`". Import-linter sees Python imports, not file reads.
- **Decision:** three layers. (1) `dce.strategy` is the only reader of `strategy_modes.yaml`. (2) `.importlinter` forbids `dce.{forecast,capacity,response,metrics,demand}` from importing `dce.strategy`, `dce.runner`, or any strategy-aware module (transitively). (3) A test scans upstream source for `strategy_modes` / `dce.strategy` so a direct YAML read can't bypass the graph. Behavioral check: `dce.runner.forecast_stage` receives the full `RunConfig` (mode included) and the test asserts identical `DatasetForecast.artifact_hash()` across all modes, plus a sensitivity check (different seed → different hash) so the test can't pass vacuously. A planted violation was confirmed to fail both the linter and the test.
- **Consequences:** `config/strategy_modes.yaml` created now with ARCH §7 values plus a `CUSTOM` entry; pydantic validation in T5.2.
- **Refs:** IDEATION P1; ARCH §1, §9.4; D-001, D-013; T2.8

### D-029 · In-house capacity model details · 2026-09-29 · accepted
- **Context:** ARCH §5.5 fixes the model; two details are open: which batch date `capacity_plan.week_start` refers to, and how parameter uncertainty enters paths.
- **Decision:** the plan's week is inferred from history per dataset: compare planned batch counts in history weeks with realized counts by batch start week vs end week; the lower mean absolute difference wins, ties → output (end) week; override via `config/app.yaml: capacity.plan_alignment`. With start alignment, output lands `median(batch duration)` weeks later. Estimates use batches ending in the last 104 weeks. p_fail ~ Beta(1 + failed, 1 + non-failed), **one draw per path** held for the whole horizon (a bad-luck quarter is a coherent scenario, not independent weekly noise). Yield ratio is bootstrapped from non-failed batches (`partial` included). Plan lines without batch history get the prior and ratio 1.0. Batch rows are iterated in a fixed sort order because RNG draws depend on it (a determinism bug caught by test).
- **Refs:** ARCH §5.5; PRD FR-11; T3.1

### D-030 · Co-man timing and reliability · 2026-09-29 · accepted
- **Decision:** earliest activation = max(decision week, Monday of `available_from`); first output = activation + `lead_time_weeks`. A weekly volume is 0 or clipped to [min_commit, max]. Reliability = delivered/requested per activity week (weeks with 0 requested ignored; over-delivery capped at 1 so plans never count on it), sampled i.i.d. per path × week (weekly delivery slips look idiosyncratic, unlike batch failure rates). No activity → Beta(9, 1) prior (mean 0.9, config `capacity.coman_reliability_prior`), flagged `reliability_from_prior`.
- **Refs:** ARCH §5.5, §5.7 constraint 8; PRD FR-12; T3.2

### D-031 · Capacity forecast scope and perishability · 2026-09-29 · accepted
- **Decision:** the capacity forecast covers the single production product line (A-001); more than one raises `NotImplementedError` pointing at A-001 rather than silently picking one. Shelf life = min over that line's production SKUs; carryover = 0 if < 7 days, else min(⌊shelf/7⌋, `capacity.carryover_cap_weeks` = 1 per A-003). Waste cost = `capacity.waste_cost_inr_per_kg`, defaulting to the unit production cost (value lost on expiry). `at_quantile(q)` is the optimizer's `Cap_in` accessor. `runner.capacity_stage` receives the full RunConfig but uses only app + seed; capacity hashes are asserted equal across modes too (P1 covers CAP).
- **Refs:** ARCH §1, §5.5; PRD FR-13; A-001, A-003; T3.3

### D-032 · Response model fitting · 2026-09-29 · accepted
- **Context:** ARCH §5.6 specifies adstock + Hill with a spend-free organic baseline, bootstrap uncertainty, and extrapolation/confounding guards.
- **Decision:**
  - *Organic baseline* = intercept + linear trend + 2 Fourier pairs of the 52-week season (week-of-year aligned), estimated **jointly** with the lift (a separate spend-free fit would soak up part of the spend effect).
  - *Estimation:* variable projection: L-BFGS-B over (θ ∈ [0, 0.9], α ∈ [0.5, 3], log κ within [0.1, 10] × mean adstock) from 4 starts; organic coefficients unconstrained and β ≥ 0 solved exactly by NNLS inside.
  - *Inputs:* weekly anomaly-cleaned D2C demand per region (summed over SKUs) and realized D2C spend (all campaign types pooled, per ARCH v1).
  - *Uncertainty:* moving-block (8-week) residual bootstrap, 40 refits; P5–P95 for θ, α, κ, β and lift at mean spend. Coverage verified across replications (a single 90% interval may miss).
  - *Guards:* `low_confidence` with reasons if spend CV < 0.10, if elasticity at mean spend is outside [0, 0.8], or if the bootstrap lift CI width / median > 1.5 (or median ≤ 0). Spend cap = max observed weekly spend × 1.5. Regions with < 52 weeks of D2C demand or no spend are skipped with a reason.
  - `steady_lift(s)` evaluates lift at constant weekly spend s (adstock s/(1−θ)); `lag_weights(n)` gives the geometric lag profile for the optimizer.
- **Refs:** IDEATION §7.2 (causality caution); ARCH §5.6; T4.1

### D-033 · Linearizing S-shaped responses · 2026-09-29 · accepted
- **Context:** Hill with α > 1 is S-shaped; its PWL would have increasing slopes and break the LP (ARCH §5.6 assumes concavity).
- **Options:** (A) constrain α ≤ 1 in the fit (biases fits of genuinely S-shaped responses); (B) MILP with SOS2 (slower, against D-006); (C) upper concave envelope.
- **Decision:** C. The PWL is built on the least concave majorant of the steady-state curve over [0, cap] (identical to the curve when α ≤ 1). Breakpoints sit at equal arc length of the normalized (spend/cap, lift/max) curve; equal-spend spacing missed the steep start of α < 1 curves (12% error) and equal-lift spacing missed the flat top of S-curves (14%). With K = 6: 2–6% max error for concave fits; strong S-curves keep the unavoidable envelope gap (reported per region as `max_abs_error`).
- **Consequences:** below an S-curve's inflection the LP sees slightly optimistic lift at small spend; it only matters for spend levels an optimizer wouldn't choose on the true curve anyway.
- **Refs:** ARCH §5.6, §5.7; D-006; T4.2

### D-034 · PuLP pinned to 2.9 with HiGHS via highspy; duals read from the live model · 2026-09-29 · accepted
- **Context:** PuLP 4.0 (current) is a Rust-core rewrite with an incompatible modeling API; PuLP 2.9's HiGHS interface doesn't populate `constraint.pi`.
- **Decision:** pin `pulp>=2.9,<3` + `highspy`. After an LP solve, duals are read from `prob.solverModel.getSolution().row_dual` in `prob.constraints` order (the order PuLP adds rows); shadow price = −row_dual for maximization. Verified on a textbook LP (duals 2 and 1). HiGHS runs single-threaded (determinism), 10 s limit, 0.5% MIP gap.
- **Consequences:** upgrading PuLP needs a port; the dual-extraction trick is covered by a test.
- **Refs:** ARCH §2, §5.7; D-006; T5.1, T5.6

### D-035 · Allocation LP conventions · 2026-09-29 · accepted
- **Context:** ARCH §5.7 leaves several modeling details implicit.
- **Decision:**
  - *Planning months:* the 13-week horizon is split 4-4-5 (`optimize.month_weeks`). Weekly detail stays in risk/stress.
  - *Monthly parameters* are quantiles of monthly **sums** of paths (capacity at `q_capacity`, B2B orders at `q_demand_b2b`, D2C demand at `q_demand_d2c`, default 0.5): a month's P15 capacity is the 15th percentile of the month's total, not the sum of weekly P15s (which would be far too pessimistic).
  - *Commit[a,m]* = forecast B2B orders (ratio × commitment) at the quantile, per ARCH; the service floor applies to it.
  - *Constraint 1* is an equality: all capacity is allocated, carried, or wasted. Initial inventory = 0 (not in the contract). End-of-horizon inventory is allowed up to the carry limit (it can be sold the week after). Carry limit = carryover weeks × average weekly capacity of the month.
  - *Goodwill cost* g_r = 25% of the D2C price per unmet kg (config). *Reach bonus* = w_reach × mean price × normalized reach (ln(1 + outlets) × regions served, scaled to [0, 1]) so it's commensurate with ₹.
  - *Soft floor:* y + f ≥ φ·Commit with f penalized at ₹1e6/kg and reported via `floor_violations()`. Floors apply only where the account is eligible. Concentration uses in-house + co-man supply once co-man exists.
  - *Eligibility (10):* D2C needs the (SKU, D2C, region) matrix window **and** `cold_chain_available`; B2B needs (SKU, B2B, account's primary region). Ineligible variables get upper bound 0.
- **Refs:** ARCH §5.7; PRD FR-14, FR-17; T5.1

### D-036 · Strategy mode schema · 2026-09-29 · accepted
- **Decision:** `dce.strategy.models.ModeConfig` (pydantic, closed schema): quantiles in (0, 1), weights ≥ 0 over exactly {rev, pen, gw, spend, reach, cust}, floor/shares in [0, 1], `res_gate` in [−5, 5] (z units), optional `q_demand_d2c` (default 0.5), `onboarding_aqs_weights` cross-checked against `scoring.yaml` profiles, `onboarding_policy` ∈ {normal, paused_unless_exceptional}. CUSTOM = the YAML `CUSTOM` base plus user overrides (weights merged key-wise), re-validated; overrides on named modes are refused so a "GROWTH" run always means the configured GROWTH. `build_run_config` validates the mode; `mode_config` in the run record is the validated dump.
- **Refs:** IDEATION §8; ARCH §7; PRD FR-15; T5.2

---

## 2. Assumptions register

### A-001 · Only cultivated chicken is commercial; other lines are R&D and enter via the product matrix · open
- **Impact if wrong:** multi-product capacity sharing needed in v1.
- **Validate:** confirm with Biokraft.

### A-002 · Units kg, currency INR, weeks start Monday · open

### A-003 · Shelf life limits carryover to ≤ 1 week by default · open
- **Impact if wrong:** frozen products allow buffering, which changes surplus/shortfall dynamics significantly.
- **Validate:** product format (fresh vs. frozen) and shelf life.

### A-004 · Co-manufacturing is available as an option with lead time, minimum commitment, and cost premium · open

### A-005 · B2B agreements have committed monthly volumes and shortfall penalties · open
- **Impact if wrong:** B2B becomes spot demand and needs forecasting like D2C.

### A-006 · Regions and their cold-chain availability are defined by the data · open

### A-007 · Market-access and regulatory status for selling cultivated chicken is taken as given by the problem statement; not verified by this project · open

### A-008 · Platform-attributed revenue in marketing data is biased upward and is not used as ground truth · open

### A-009 · Demand of different regions is independent in the v1 stress test (no cross-region correlation) · open
- **Impact if wrong:** underestimates joint shortfall risk.
- **Validate:** residual correlation analysis in backtests (T2.6); if significant, sample joint residuals.

### A-010 · B2B contracts are not assumed to renew within the horizon · open
- **Why assumed:** the contract carries no renewal field; assuming renewal would create demand the company has no agreement for.
- **Impact if wrong:** B2B demand is understated after `contract_end`, freeing capacity that may actually be needed.
- **How to validate:** ask Biokraft for renewal rates (Q-004); a renewal-probability field could be a contract change.

---

## 3. Open questions for Biokraft / problem-statement owners

- Q-001 · Actual share of D2C vs. B2B today, and which B2B account types matter most?
- Q-002 · Product format (fresh / frozen / ready-to-cook) and shelf life?
- Q-003 · Existing or candidate co-manufacturing partners: lead times, minimums, cost premium?
- Q-004 · Typical B2B contract terms: commitment, penalties, duration?
- Q-005 · Which marketing channels are used today, and in which regions?
- Q-006 · How does Biokraft define "reach" for a B2B account (outlets, cities, footfall, brand visibility)?
- Q-007 · Planning cadence: monthly or weekly decisions? Who approves?
- Q-008 · Expected timeline for the next product line to reach pilot/production?

---

## 4. Contract change requests

### CC-001 · 2026-09-29 · status: requested
- **Change:** clarifications of v1 semantics that ARCH §3 leaves implicit (no field added or removed):
  1. CSV encoding: UTF-8, header row, column order free; null = empty cell; booleans `true`/`false` (case-insensitive, `1`/`0` accepted); integer columns accept integral floats (`3.0`).
  2. `manifest.json` is a closed schema with exactly: `world_id`, `contract_version`, `start_date`, `end_date`, `generated_at` (ISO 8601 datetime), optional `files` (name → SHA-256). Any other key is rejected, so the manifest cannot carry regime information.
  3. Foreign keys: `orders.customer_id` → `customers` (D2C rows), `orders.account_id` → `b2b_accounts` (B2B rows); `nps_responses.customer_or_account_id` → `customers` or `b2b_accounts` by `channel`; `b2b_accounts.regions_served` elements → `regions`; `coman_activity.coman_id` → `coman_contracts`; all `region_id`/`sku_id` columns → their tables.
  4. Row rules (errors): D2C orders have `customer_id` and no `account_id`, B2B the reverse; failed batches have `actual_yield_kg = 0`; `end_date ≥ start_date` for batches, contracts, eligibility windows; `min_commit ≤ max` for co-man; `churned_date ≥ acquired_date`; `week_start` columns are Mondays.
  5. Natural keys (duplicates are warnings, summed): capacity_plan, coman_activity, marketing_plan, marketing_daily, product_matrix.
- **Reason:** the app must reject malformed drops deterministically and with locations; the DGP side needs the same reading to emit conforming files. Full rendering in `contract/README.md`.
- **Version bump:** none (1.0.0); clarifications only. To be confirmed by the DGP side.

## 5. Integrity events

*(none yet)*

## 6. Tuning notes

*(none yet)*

## 7. Evaluation log

*(none yet)*

## 8. Task log

*(append TL entries below as tasks complete)*

### TL-001 · T0.1 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** Scaffolded repo per ARCH §4: uv workspace + `backend/dce` package with all module dirs, typer CLI (`dce version`), Next.js 16 + Tailwind app in `frontend/`, Makefile (setup/test/lint/fmt/run-api/run-web), ruff + mypy + import-linter, pre-commit (incl. DGP-artifact guard), `.gitignore`, `.env.example`, `config/app.yaml`.
- **Files touched:** `pyproject.toml`, `backend/pyproject.toml`, `backend/dce/**`, `frontend/**`, `Makefile`, `.gitignore`, `.pre-commit-config.yaml`, `.importlinter`, `config/app.yaml`, `CLAUDE.md`, docs moved to `docs/`
- **Tests:** 1 added (smoke) / 1 passing; `make lint` clean
- **Decisions made:** D-012, D-013
- **Deviations from ARCH:** none (D-012 resolves a README/ARCH path mismatch)
- **Known issues / follow-ups:** `make run-api` target references `dce.api.app`, which lands in T8.1; uvicorn not yet a dependency.

### TL-002 · T0.2 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** Data contract v1: Python spec for all 13 tables + manifest (`dce.contract.spec`), generated JSON Schemas (draft 2020-12) and `contract/README.md`, validator (string read → typed cast → pandera → row rules → FKs → manifest) with issue locations. CLI: `dce contract export`, `dce contract check <dir>`.
- **Files touched:** `backend/dce/contract/{spec,export,validate}.py`, `backend/dce/cli.py`, `contract/**`, `backend/dce/tests/test_contract.py`
- **Tests:** 16 added / 17 passing (valid world; wrong type; bad date/int; missing column; missing file; bad enum; FK; list-FK + conditional FK; null; duplicate PK; range + row rules + Monday; closed manifest; major version; JSON Schema validity; contract drift)
- **Decisions made:** D-014, D-015; CC-001 requested
- **Deviations from ARCH:** pandera models built at runtime rather than stored in `contract/models.py` (D-014)
- **Known issues / follow-ups:** CC-001 must be shared with the DGP side (`contract/README.md`).

### TL-003 · T0.3 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `data/fixtures/tiny_world/` generated by a deterministic toy script (`data/fixtures/make_tiny_world.py`, seed 7): 2 regions, 1 SKU, 3 accounts (2 active, 1 pipeline), 2 co-man partners (one with 8 weeks of activity), 110 weeks; one stockout week (w50: cancelled_stockout + partial D2C, partial B2B), one waitlist week (w51), one failed batch (w40). Labeled by `NOT_FOR_EVALUATION` marker and script docstring.
- **Files touched:** `data/fixtures/make_tiny_world.py`, `data/fixtures/tiny_world/*`, `backend/dce/tests/test_fixtures.py`, root `pyproject.toml` (ruff config for non-backend scripts)
- **Tests:** 4 added / 21 passing; regeneration is byte-identical
- **Decisions made:** none
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** T11 evaluator should refuse any world folder containing `NOT_FOR_EVALUATION`.

### TL-004 · T0.4 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** SQLModel tables (datasets, runs, run_artifacts, recommendations, decisions, outcomes, scenarios) with FK enforcement; run lifecycle (`start_run`/`finish_run`/`add_artifact`); dataset + config hashing; git SHA capture; structlog JSON logging with contextvar binding; seed derivation.
- **Files touched:** `backend/dce/{hashing,seeds,config,logs,gitinfo}.py`, `backend/dce/store/{models,db,runs}.py`, `backend/dce/tests/test_store.py`
- **Tests:** 7 added / 28 passing
- **Decisions made:** D-016
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** no migrations (schema created with `create_all`); revisit if the schema changes after data exists.

### TL-005 · T1.1 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce ingest <world>`: path guard → contract validation → continuity checks → `validation_report.{json,md}` → Parquet under `data/processed/<dataset_hash>/` → `datasets` row. `load_dataset(hash)` for downstream modules. tiny_world ingests with 0 errors / 0 warnings.
- **Files touched:** `backend/dce/ingest/{core,checks,__init__}.py`, `backend/dce/cli.py`, `backend/dce/tests/test_ingest.py`
- **Tests:** 13 added / 41 passing (CLI end-to-end; idempotent hash; guard rejects `../`, `/etc`, `/tmp`, `../dgp`, root dirs, symlink escape; accepts relative + absolute inside; invalid world → report, no Parquet; short history → error)
- **Decisions made:** D-017
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-006 · T1.2 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.demand.reconstruct.weekly_demand`: zero-filled weekly demand vs. sales per series (D2C region×SKU; B2B account×SKU×region) with `censored_kg`, `censored_share`, `is_censored`, `unmet_kg`, `partial_short_kg`, `cancelled_other_kg`; `channel_totals` for bottom-up totals. `ingest.load_history_window` / `load_manifest` helpers. Session-scoped tiny_world ingest fixture in `conftest.py`.
- **Files touched:** `backend/dce/demand/reconstruct.py`, `backend/dce/ingest/core.py`, `backend/dce/tests/{conftest,test_demand}.py`
- **Tests:** 8 added / 49 passing (stockout week demand > sales; waitlist censored; normal weeks equal; B2B partial unmet but uncensored; zero-fill grid; conservation; cancelled_other excluded; empty input)
- **Decisions made:** D-018
- **Deviations from ARCH:** `cancelled_other` excluded from demand (D-018)
- **Known issues / follow-ups:** none

### TL-007 · T1.3 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.metrics.funnel.funnel_metrics(tables, first, last, grain)` returns every §7.2 metric per region × channel × week|month on a complete grid (spend, visitors, bounce rate, CTR, CPC, CPL, qualified rate, visitor→lead, lead→customer, new/paid/organic customers, CAC, churn, AOV, frequency, margin %, LTV, LTV:CAC, MRR, NPS, ROI on margin). `dce.metrics.cohorts`: cohort table, cohort LTV (realized + projected), region LTV. `config/scoring.yaml` created (metrics section).
- **Files touched:** `backend/dce/metrics/{funnel,cohorts}.py`, `backend/dce/config.py`, `config/scoring.yaml`, `backend/dce/tests/test_metrics_funnel.py`
- **Tests:** 12 added / 61 passing (hand-computed values for every formula; zero denominators → null; no inf/NaN anywhere; weekly grain; cohort LTV realized/projected/capped; config load; tiny_world smoke)
- **Decisions made:** D-019
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** D2C period LTV is noisy at week grain by construction; RES should use cohort LTV.

### TL-008 · T1.4 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** RES (`dce.metrics.res`): step-up detection, sustained-lift ratio, econ/retention/NPS components, within-channel z-scores, empirical-Bayes shrinkage with confidence, component breakdown + missing list. AQS (`dce.metrics.aqs`): volume/stability/reach/margin/reliability/penalty/concentration with profiles, type priors for pipeline accounts (`prior=true`). `dce.metrics.scores.evidence_scores` orchestrates from a loaded dataset. Fixed an unsigned-integer wrap in funnel counts (NPS with more detractors than promoters wrapped to ~7e10); all funnel counts are now Int64.
- **Files touched:** `backend/dce/metrics/{res,aqs,scores,funnel,cohorts}.py`, `config/scoring.yaml`, `backend/dce/tests/{test_scores,test_metrics_funnel}.py`
- **Tests:** 14 added / 75 passing (sustained ≈1, spike ≈0, noise/blip ignored, no-response uninformative; low-n region shrunk toward mean while high-n barely moves; breakdown columns; pipeline prior flag + confidence 0; concentration flag lowers AQS; profiles change AQS not components; config load; last complete month; negative-NPS regression)
- **Decisions made:** D-020
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** RES lift ignores seasonality around step-ups (confounding caveat, IDEATION §7.2); revisit with the T4 response model.

### TL-009 · T2.1 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.forecast.backtest`: `Fold`, `rolling_origin_folds`, `Forecaster` protocol, `run_backtest` (shape-checked predictions joined to actuals), `mase_scale`, `pinball`, `score_backtest` (MASE, pinball per quantile + mean + scaled, coverage). Forecast settings added to `config/app.yaml`.
- **Files touched:** `backend/dce/forecast/backtest.py`, `config/app.yaml`, `backend/dce/tests/test_backtest.py`
- **Tests:** 7 added / 82 passing (fold geometry; short-history fold dropping; no-leakage spy model; shape enforcement; MASE scale incl. fallback and constant series; hand-computed MASE/pinball/coverage)
- **Decisions made:** D-021
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-010 · T2.2 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.forecast.baselines`: `SeasonalNaive(52)` and `WindowAverage(8)` implementing the `Forecaster` protocol, P50 = rule, P10/P90 from empirical (h-step where relevant) residuals, non-negative and non-crossing.
- **Files touched:** `backend/dce/forecast/baselines.py`, `backend/dce/tests/test_baselines.py`
- **Tests:** 7 added / 89 passing (seasonal copy; residual quantiles vs numpy; short-history fallback; window mean; hand-computed h=1 residuals; ordering + non-negativity; backtest smoke on fixture)
- **Decisions made:** D-022
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-011 · T2.3 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.forecast.statistical`: `auto_ets()` / `auto_theta()` wrappers (statsforecast 2.0.1) implementing the `Forecaster` protocol. Shared `PREDICTION_SCHEMA` / `empty_prediction()` in `backtest.py`.
- **Files touched:** `backend/dce/forecast/{statistical,backtest,baselines}.py`, `backend/pyproject.toml` (+statsforecast), `backend/dce/tests/test_statistical.py`
- **Tests:** 4 added / 93 passing (contract shape, ordering, non-negativity, no NaN incl. all-zero and early-ending series; ETS extrapolates a trend; backtest smoke)
- **Decisions made:** D-023
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-012 · T2.4 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.forecast.lgbm.LightGBMQuantile` (direct multi-horizon global quantile model, three quantile boosters), `dce.forecast.covariates` (known-at-forecast-time covariate frame), `dce.forecast.calendar` (weekly Indian holiday counts). Deps: lightgbm 4.7, holidays 0.105.
- **Files touched:** `backend/dce/forecast/{lgbm,covariates,calendar,baselines,backtest}.py`, `backend/pyproject.toml`, `backend/dce/tests/test_lgbm.py`
- **Tests:** 8 added / 101 passing (poisoning post-origin data leaves features unchanged; every training target lies after its origin and inside history; raising planned future spend raises the forecast; covariates unchanged by tampering with realized marketing/orders but changed by the plan; no crossing, non-negative, deterministic; small-data fallback; horizon/history alignment; backtest smoke)
- **Decisions made:** D-024
- **Deviations from ARCH:** none (lag set is ARCH's, counted from the origin in the direct formulation)
- **Known issues / follow-ups:** ~0.5 s per fit on the fixture; watch NFR-1 once the full backtest runs (T2.6).

### TL-013 · T2.5 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.forecast.anomaly`: `detect_anomalies` (robust z, flags, winsorized `y_clean`), `anomaly_report`, `AnomalyConfig` from `config/scoring.yaml`.
- **Files touched:** `backend/dce/forecast/anomaly.py`, `config/scoring.yaml`, `backend/dce/tests/test_anomaly.py`
- **Tests:** 6 added / 107 passing (injected 8× spikes in the fixture flagged and winsorized; stockout dip not flagged; B2B not flagged; forward P50 of SeasonalNaive / WindowAverage / LightGBM stays < 1.6× pre-spike level on cleaned data while the raw spike leaks into naive rules; constant/zero series never flagged; < 1% false flags on Gaussian noise)
- **Decisions made:** D-025
- **Deviations from ARCH:** residual source (D-025)
- **Known issues / follow-ups:** none

### TL-014 · T2.6 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.forecast.selection` (baseline-gated selection with reasons), `dce.forecast.calibration` (split-conformal per series/pooled, application, cross-fit coverage), `dce.forecast.paths` (block bootstrap, tail rescaling, long-form writer), `dce.forecast.pipeline` (`ForecastConfig`, `run_forecast` → `ForecastSet` with quantiles incl. raw bands, paths, selection, scores, coverage, calibration, anomalies; `artifact_hash`, `write`).
- **Files touched:** `backend/dce/forecast/{selection,calibration,paths,pipeline}.py`, `backend/dce/tests/test_forecast_pipeline.py`
- **Tests:** 11 added / 118 passing (selection incl. MASE gate, fallback and undefined baseline; conformal widening to ≈80% under cross-fit; pooling of small series; adjustment ordering/non-negativity; block structure preserved; path quantiles match calibrated bands; deterministic + seed-sensitive hash; artifact writing; perfect-seasonal series falls back to baseline; full fixture run with all five models)
- **Decisions made:** D-026
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** fixture run with all five models takes ~5.5 s for 4 series; profile at realistic scale for NFR-1.

### TL-015 · T2.7 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.forecast.b2b` (eligibility with reasons, ratio series, per-account ratio distribution (weekly and monthly), ratio forecast × commitment, contract-end zeroing in quantiles and paths). `dce.forecast.run.forecast_dataset` → `DatasetForecast` combining D2C and B2B (kg quantiles, paths, artifact hash, writer). Pipeline now handles series without backtest history (baseline + pooled calibration + band-derived paths).
- **Files touched:** `backend/dce/forecast/{b2b,run,pipeline,calibration,paths}.py`, `backend/dce/tests/test_b2b_forecast.py`
- **Tests:** 9 added / 127 passing (eligibility + reasons; ratio distribution; kg = ratio × commitment; churned and paused excluded; contract end zeroes quantiles and paths; expired contract excluded; young account → baseline with non-zero spread; dataset forecast combines channels, deterministic, writes artifacts)
- **Decisions made:** D-027, A-010
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-016 · T2.8 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `config/strategy_modes.yaml` (GROWTH, STABILITY, D2C_EXPANSION, CUSTOM); `dce.strategy` loader; `dce.runner` (`RunConfig`, `build_run_config`, `forecast_stage`); import-linter contract extended to `dce.strategy` and `dce.runner`; mode-invariance tests.
- **Files touched:** `config/strategy_modes.yaml`, `.importlinter`, `backend/dce/strategy/__init__.py`, `backend/dce/runner.py`, `backend/dce/tests/test_mode_invariance.py`
- **Tests:** 9 added / 136 passing (all modes defined; identical forecast hash across 4 modes; hash sensitivity; source scan of 5 upstream packages; import-linter contract via subprocess)
- **Decisions made:** D-028
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** the invariance test runs the forecast 6× (~25 s); acceptable, but the suite is getting slower.

### TL-017 · T3.1 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.capacity.inhouse`: `InHouseConfig`, plan-alignment inference, Beta-Binomial failure + empirical yield estimation per facility × line, `simulate_inhouse` → `InHouseCapacity` (line paths [L, P, H], planned kg, params frame with p_fail CI and ratio quantiles, total paths per product line). Capacity config added to `config/app.yaml`.
- **Files touched:** `backend/dce/capacity/inhouse.py`, `config/app.yaml`, `backend/dce/tests/test_capacity_inhouse.py`
- **Tests:** 7 added / 143 passing (posterior counts; ratios exclude failures; path mean = planned × ratio × (1 − p̄_fail); bounds; deterministic by seed; alignment inference both ways; start-aligned output shift; prior for lines without history; fixture smoke)
- **Decisions made:** D-029
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-018 · T3.2 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.capacity.coman`: `CoManPartner` (activation/output timing, horizon output mask, volume snapping, reliability sampling, delivered paths, summary) and `load_partners` from contracts + activity.
- **Files touched:** `backend/dce/capacity/coman.py`, `backend/dce/tests/test_capacity_coman.py`
- **Tests:** 5 added / 148 passing (lead time + availability incl. mid-week `available_from`; output mask; volume bounds; empirical haircut incl. over-delivery cap and 0/0 rows; prior when no history; fixture)
- **Decisions made:** D-030
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** min-active-weeks linking is an optimizer constraint (T5.4).

### TL-019 · T3.3 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.capacity.model`: `carryover_weeks`, `Perishability`, `CapacityForecast` (in-house paths + quantiles incl. planned kg and mean, co-man partners for the line, `at_quantile`, artifact hash, writer), `capacity_forecast`; `runner.capacity_stage`.
- **Files touched:** `backend/dce/capacity/model.py`, `backend/dce/runner.py`, `backend/dce/tests/test_capacity_model.py`
- **Tests:** 11 added / 159 passing (carryover rule table; failure-heavy history widens P10–P90 > 1.5× and lowers P50; quantile ordering + accessor; perishability from fixture; deterministic hash; capacity hash identical across modes; artifact writing)
- **Decisions made:** D-031
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-020 · T4.1 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.response.fit` (`ResponseConfig`, adstock, Hill, organic design, variable-projection fit, block bootstrap, guards, `ResponseFit` with steady-state lift, lag weights, summary) and `dce.response.run` (`weekly_region_inputs`, `fit_responses` → `ResponseSet` with hash); `runner.response_stage`. Response settings in `config/app.yaml`.
- **Files touched:** `backend/dce/response/{fit,run}.py`, `backend/dce/runner.py`, `config/app.yaml`, `backend/dce/tests/test_response_fit.py`
- **Tests:** 8 added / 167 passing (recovers test-only θ/α/κ/β and the lift curve within 12%; CI coverage ≥ 60% over 8 replications for θ and lift; cap + lag weights; constant spend → low_confidence; no true effect → low_confidence; implausible-elasticity flag; deterministic; fixture driver + identical hash across modes)
- **Decisions made:** D-032
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** full test suite now ~65 s; consider pytest-xdist.

### TL-021 · T4.2 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.response.pwl`: `concave_majorant`, arc-length breakpoints, `linearize(fit, k)` → `ResponseCurve` (breakpoints, widths, non-increasing slopes, cap, low_confidence, max_abs_error, `evaluate`). `response.pwl_segments: 6` in config.
- **Files touched:** `backend/dce/response/pwl.py`, `config/app.yaml`, `backend/dce/tests/test_response_pwl.py`
- **Tests:** 9 added / 176 passing (K segments, non-increasing non-negative slopes, cap = max × 1.5, no lift beyond cap, for α ∈ {0.6, 1, 1.6, 2.8}; PWL is a concave, monotone function; concave fits: chords below the curve with error < 10% of max; S-curve envelope; majorant unit case; zero response)
- **Decisions made:** D-033
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-022 · T5.1 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.optimize.solver` (HiGHS wrapper + named duals), `dce.optimize.inputs` (`Month`, `split_months`, `ModeParams`, `PlanInputs`, `monthly_quantile`, eligibility, `build_inputs` from forecast + capacity + tables), `dce.optimize.lp` (`build_lp` / `finalize_lp` with extension hooks for supply and demand terms, `solve_allocation` → `PlanResult` with allocation table, floor violations, duals). Optimizer settings in `config/app.yaml`.
- **Files touched:** `backend/dce/optimize/{solver,inputs,lp}.py`, `config/app.yaml`, `backend/pyproject.toml` (pulp 2.9, highspy), `backend/dce/tests/test_optimize_lp.py`
- **Tests:** 8 added / 184 passing (surplus → carry then waste; mode-dependent D2C/B2B tradeoff matches hand-derived marginal values; infeasible floor soft + reported; concentration cap; ineligible region; carryover; 20 random instances satisfy balance and demand bounds; fixture end-to-end with duals)
- **Decisions made:** D-034, D-035
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none

### TL-023 · T5.2 · 2026-09-29
- **Agent/author:** Claude Code
- **Summary:** `dce.strategy.models` (`Weights`, `ModeConfig`, `validate_modes`, `custom_mode`); `dce.strategy.load_modes` / `resolve_mode`; `runner.build_run_config(mode, seed, mode_overrides=…)` now validates.
- **Files touched:** `backend/dce/strategy/{__init__,models}.py`, `backend/dce/runner.py`, `backend/dce/tests/test_strategy_modes.py`
- **Tests:** 12 added / 196 passing (YAML validates; 8 bad-value cases rejected; unknown AQS profile; CUSTOM overrides + validation + refusal on named modes + unknown mode; run config → optimizer params)
- **Decisions made:** D-036
- **Deviations from ARCH:** none
- **Known issues / follow-ups:** none


# TASK — Biokraft Demand-Capacity Engine (DCE)

## Rules for whoever executes these tasks (human or AI agent)

1. Work **in order**. Do not start a task until its dependencies are `[x]`.
2. Before starting a task, re-read the relevant sections of IDEATION / ARCH listed under **Refs**.
3. After finishing a task:
   - tick its checkbox here,
   - append a **Task Log** entry in `docs/NOTES.md` (template there),
   - record any design choice not already specified as a **Decision** in NOTES.md,
   - run `make test` (all tests must pass).
4. If a task requires deviating from ARCH, **stop**, write a Decision entry proposing the change, then proceed.
5. **Never** open, fetch, or search for anything from the `biokraft-dgp` repository or its generator. The only data source is `data/incoming/` (real drops) and `data/fixtures/` (hand-written unit-test data). If you see DGP internals, log an Integrity Event.
6. Fixtures are for testing code paths only. Never report accuracy or business results on fixtures.
7. Keep each commit scoped to one task ID: `T2.4: lightgbm quantile model`.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[-]` dropped (with Decision entry)

---

## Phase 0 — Foundations

- [x] **T0.1 Scaffold repo**
  *Refs:* ARCH §2, §4
  *Do:* create the layout from ARCH §4; `uv` project in `backend/`; Next.js app in `frontend/`; Makefile (`make setup`, `make test`, `make lint`, `make run-api`, `make run-web`); ruff + mypy; pre-commit; `.gitignore` for `data/incoming`, `data/processed`, `.env`.
  *Accept:* `make setup && make test` passes with an empty test suite; `make lint` is clean.

- [x] **T0.2 Data contract v1**
  *Refs:* ARCH §3
  *Do:* JSON Schemas + pandera models for all 13 files + `manifest.json`; `CONTRACT_VERSION = 1.0.0`; `contract/README.md` rendering the tables.
  *Accept:* schemas validate a correct fixture and reject each of: wrong type, missing column, bad enum, FK violation.
  *Depends:* T0.1

- [x] **T0.3 Unit-test fixtures**
  *Do:* `data/fixtures/tiny_world/`: 2 regions, 1 SKU, 3 accounts, 2 co-man partners, 110 weeks; hand-written or simple deterministic script, **clearly labeled not for evaluation**. Include one stockout week, one waitlist, and one failed batch so code paths are exercised.
  *Accept:* passes T0.2 validation.
  *Depends:* T0.2

- [x] **T0.4 Run + store infrastructure**
  *Refs:* ARCH §5.12, §8
  *Do:* SQLite models; `run_id` generation; dataset/config hashing; git SHA capture; structlog setup; seed derivation utility.
  *Accept:* a dummy run writes a row with all hashes; the same inputs give the same hashes.
  *Depends:* T0.1

## Phase 1 — Ingestion and metrics

- [x] **T1.1 Ingestion + validation report**
  *Refs:* ARCH §5.1 · PRD FR-1, FR-3
  *Accept:* `dce ingest data/fixtures/tiny_world` outputs a report (JSON + readable summary) and Parquet; the path guard rejects `../` and absolute paths outside allowed dirs (test).
  *Depends:* T0.3, T0.4

- [x] **T1.2 Demand reconstruction**
  *Refs:* ARCH §5.2 · IDEATION §13 (unconstrained demand) · PRD FR-2
  *Accept:* weekly requested vs. fulfilled series; censoring flags; test that the stockout week shows demand > sales.
  *Depends:* T1.1

- [x] **T1.3 Funnel metrics engine**
  *Refs:* IDEATION §7.2 · ARCH §5.3 · PRD FR-4
  *Do:* every metric in the IDEATION table, per region × channel × week/month. Cohort LTV. ROI on margin.
  *Accept:* unit tests with hand-computed expected values for each formula; divide-by-zero handled (returns null, not inf).
  *Depends:* T1.1

- [x] **T1.4 Evidence scores (RES, AQS)**
  *Refs:* IDEATION §9 · ARCH §5.3 · `config/scoring.yaml`
  *Accept:* scores + component breakdown + confidence; shrinkage test (low-n region is pulled toward the mean); pipeline accounts flagged `prior=true`.
  *Depends:* T1.3

## Phase 2 — Forecasting

- [x] **T2.1 Backtest harness**
  *Refs:* ARCH §5.4 · PRD FR-7
  *Accept:* rolling-origin splitter (no leakage test: every feature timestamp < fold cutoff); MASE, pinball, coverage implemented and unit-tested.
  *Depends:* T1.2

- [x] **T2.2 Baselines**: SeasonalNaive(52), WindowAverage(8), with quantiles from empirical residuals.
  *Depends:* T2.1

- [x] **T2.3 Statistical models**: AutoETS, AutoTheta via statsforecast.
  *Depends:* T2.1

- [x] **T2.4 LightGBM global quantile model**
  *Refs:* ARCH §5.4 features list
  *Accept:* uses planned spend (known future) but never realized future data (test); three quantile models or one with quantile objective per q; no quantile crossing (sort fix + test).
  *Depends:* T2.1

- [x] **T2.5 Anomaly detection and handling**
  *Refs:* IDEATION §7.2 (viral content) · PRD FR-9
  *Accept:* injected spike in a fixture is flagged, winsorized for training, and not present in forward P50.
  *Depends:* T2.2

- [x] **T2.6 Model selection + conformal calibration + sample paths**
  *Refs:* ARCH §5.4 · PRD FR-8, FR-6
  *Accept:* per-series selection logged; baseline fallback works; calibrated coverage reported; `n_paths` block-bootstrapped paths produced.
  *Depends:* T2.3, T2.4, T2.5

- [x] **T2.7 B2B account forecasting**
  *Refs:* ARCH §5.4 (order-to-commitment ratio)
  *Accept:* per-account ratio distribution; churned/paused accounts excluded; contract end dates respected.
  *Depends:* T2.6

- [x] **T2.8 Mode-invariance guard**
  *Refs:* IDEATION P1 · ARCH §9.4
  *Accept:* test runs the forecast stage under every mode and asserts identical artifact hashes. Forecast module has no import path to `config/strategy_modes.yaml` (import-linter rule).
  *Depends:* T2.6

## Phase 3 — Capacity

- [x] **T3.1 In-house yield model** (Beta-Binomial failure, empirical yield ratio, paths aligned with forecast horizon). *Depends:* T1.1
- [x] **T3.2 Co-man model** (lead time, min/max, reliability haircut from `coman_activity`). *Depends:* T1.1
- [ ] **T3.3 Capacity quantiles + perishability config**
  *Accept:* capacity quantiles and paths; carryover rule from shelf life; test that a failed-batch-heavy history widens the P10–P90 band.
  *Depends:* T3.1, T3.2

## Phase 4 — Response model

- [ ] **T4.1 Adstock + Hill fit per region**
  *Refs:* ARCH §5.6
  *Accept:* recovers parameters on a fixture generated with **known test-only parameters inside the test file** (this is a unit test of the fitter, not evaluation); bootstrap CIs; `low_confidence` flag logic.
  *Depends:* T1.3

- [ ] **T4.2 Piecewise linearization + extrapolation guard**
  *Accept:* K segments with non-increasing slopes; spend cap = max observed × factor; test concavity.
  *Depends:* T4.1

## Phase 5 — Optimizer

- [ ] **T5.1 Core LP** (constraints 1–5, 9, 10 of ARCH §5.7; no spend, no co-man). *Depends:* T2.7, T3.3
- [ ] **T5.2 Strategy modes from YAML** (pydantic-validated config; CUSTOM mode). *Depends:* T5.1
- [ ] **T5.3 Spend co-optimization** (constraints 2, 6, 7 with response segments, lag distribution, evidence gate, exploration pool). *Depends:* T5.2, T4.2, T1.4
- [ ] **T5.4 Co-man MILP extension** (constraint 8, min-active linking). *Depends:* T5.3
- [ ] **T5.5 Soft constraints + infeasibility diagnostics.** *Depends:* T5.4
- [ ] **T5.6 Explanations** (binding constraints, shadow prices via LP relaxation, top drivers per line). *Depends:* T5.5
- [ ] **T5.7 Monte Carlo stress test.** *Depends:* T5.5
- [ ] **T5.8 Rule baselines** (proportional, B2B-first, FCFS) through the same stress test. *Depends:* T5.7
- [ ] **T5.9 Invariant + property tests**
  *Refs:* ARCH §9 items 1–3, 8, 9
  *Accept:* hypothesis-based tests pass for 200 random small instances.
  *Depends:* T5.8

## Phase 6 — Risk and mitigation

- [ ] **T6.1 Breach + surplus detector** (weekly P_breach from paths; mode thresholds). *Depends:* T2.6, T3.3
- [ ] **T6.2 Mitigation catalog** (`config/mitigations.yaml` M1–M6 with lead times, cost, harm, reversibility). *Depends:* T6.1
- [ ] **T6.3 Impact via re-solve + ranking + act-by dates**
  *Accept:* infeasible-by-lead-time mitigations are excluded (test); ranking is deterministic.
  *Depends:* T6.2, T5.7

## Phase 7 — Onboarding simulator

- [ ] **T7.1 Candidate simulation** (start months × ramp profiles; recommendation class; deltas; reasons)
  *Accept:* zero-volume candidate equals baseline (test); huge candidate violating concentration → `decline` or `phase` with reason.
  *Depends:* T5.7, T1.4

## Phase 8 — API

- [ ] **T8.1 FastAPI app + endpoints** per ARCH §6, with pydantic schemas and OpenAPI.
- [ ] **T8.2 Background run execution** (simple in-process task queue; status polling).
- [ ] **T8.3 API tests** (httpx; happy path + error mapping).
  *Depends:* T6.3, T7.1

## Phase 9 — AI layer

- [ ] **T9.1 `LLMClient` + Anthropic implementation** (env key, model from config, timeout/retry, prompt + response logging with secrets redacted).
- [ ] **T9.2 Narrative + NumberGroundingValidator + template fallback**
  *Accept:* validator test with an injected fake number → rejected; fallback template renders.
- [ ] **T9.3 Scenario parser + executor + diff**
  *Accept:* 15 example prompts (in `tests/scenarios.yaml`) parse into correct specs; unsupported requests return `unsupported`.
  *Depends:* T8.1

## Phase 10 — Frontend

- [ ] **T10.1 App shell, API client, dataset + mode selector**
- [ ] **T10.2 Overview** (demand/capacity fan charts, breach/surplus markers, KPIs, narrative)
- [ ] **T10.3 Allocation plan** (tables, spend, mode compare, baseline compare, stress results)
- [ ] **T10.4 Markets** (region scorecards with RES breakdown)
- [ ] **T10.5 B2B accounts + onboarding simulator**
- [ ] **T10.6 Alerts & mitigations**
- [ ] **T10.7 Scenarios**
- [ ] **T10.8 Decision log** (accept/reject with reason)
- [ ] **T10.9 Data & model health** (validation, backtests, coverage, anomalies)
  *Accept (all):* every number shows a tooltip with run_id + source; P10–P90 band always shown with P50.
  *Depends:* T8.3, T9.3

## Phase 11 — Evaluation across blind worlds

- [ ] **T11.1 Holdout splitter + evaluator** (ARCH §5.13; loader test that the run pipeline cannot read the holdout).
- [ ] **T11.2 Run all worlds × all modes + baselines**; produce `reports/eval_<ts>/` (tables + charts).
- [ ] **T11.3 Freeze**: record the report hash and summary in NOTES.md → Evaluation Log **before** requesting world descriptions.
- [ ] **T11.4 Unblind + retrospective**: request the sealed world manifests from the DGP side; write where the method worked or failed per regime. **Do not** modify the frozen report; add a new retrospective section instead.
  *Depends:* T10.9

## Phase 12 — Demo and polish

- [ ] **T12.1 Docker Compose one-command demo**
- [ ] **T12.2 Demo script** (5-minute walkthrough: mode switch → breach → mitigation → onboarding → what-if)
- [ ] **T12.3 README final pass + screenshots**
- [ ] **T12.4 Closed-loop demo** (two sequential data drops: run on drop 1, log decisions, ingest drop 2, show outcomes vs. predictions and RES refresh)

## Stretch (only after v1.0)

- [ ] **S1 Interactive replay**: the DGP exposes an environment API (`step(decisions) → next week's data`); the app plays a quarter against it. API-only, no internals.
- [ ] **S2 Hierarchical Bayesian response model** (PyMC) with partial pooling.
- [ ] **S3 MinT hierarchical forecast reconciliation.**
- [ ] **S4 Cold-start for new product lines** from the product matrix (priors from analog SKUs).
- [ ] **S5 Censored-demand correction** (Tobit-style) where waitlist capture is incomplete.

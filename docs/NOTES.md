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

*(none yet)*

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


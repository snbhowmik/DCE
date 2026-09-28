# PRD — Biokraft Demand-Capacity Engine (DCE)

**Version:** 0.1 (draft) · **Business logic:** see IDEATION.md · **Design:** see ARCH.md

---

## 1. Problem statement

Biokraft Foods sells cultivated chicken through D2C e-commerce and B2B wholesale, under a hard and uncertain production ceiling (in-house bioreactor batches plus optional co-manufacturing). Three coupled decisions are made today in separate spreadsheets:

- allocating monthly output between channels and regions,
- choosing where to spend marketing budget, and
- deciding when to onboard new B2B accounts.

This causes stockouts, wasted marketing spend, over-committed contracts, and surplus waste, with no learning from past decisions.

## 2. Product goal

**One AI-assisted planning view** that turns order, capacity, and marketing history into a concrete, explainable, uncertainty-aware recommendation for allocating scarce capacity and marketing spend across channels and regions. It also flags shortfalls and surpluses early enough to act.

## 3. Goals and non-goals

### Goals
- G1. Probabilistic 13-week demand forecasts by channel × region (weekly), aggregated monthly.
- G2. Probabilistic capacity forecast (in-house yield variability + co-man timing).
- G3. Allocation + marketing-spend recommendation per strategy mode (GROWTH / STABILITY / D2C_EXPANSION / CUSTOM).
- G4. B2B onboarding simulator: accept / defer / phase, with start-date and ramp recommendation.
- G5. Breach and surplus alerts with ranked, lead-time-feasible mitigations and act-by dates.
- G6. Plain-language narrative and natural-language what-if scenarios, grounded in solver output.
- G7. Decision log closing the loop between recommendations and outcomes.
- G8. Honest evaluation across multiple blind synthetic worlds.

### Non-goals (v1)
- Production scheduling at batch or bioreactor level.
- Pricing optimization and dynamic pricing.
- Creative/CTA design or ad-platform automation (no pushing spend to ad APIs).
- Logistics routing and cold-chain dispatch.
- Real ERP/e-commerce integrations (CSV ingestion only in v1).
- Building the DGP (separate repo, separate AI; see IDEATION §11).

## 4. Users

| Persona | Needs | Primary screens |
|---|---|---|
| **Founder / CEO** | Set quarterly focus; approve big bets (onboarding, co-man) | Overview, Onboarding, Decision log |
| **Ops / Production lead** | Know when capacity breaks; trigger co-man in time | Overview, Alerts, Capacity |
| **Growth / Marketing lead** | Where to spend; where to stop spending | Markets, Allocation |
| **B2B sales lead** | Can we take this account, and when? | Onboarding simulator, Accounts |

## 5. User stories

- US1. As CEO, I pick a strategy mode for the quarter and see how the allocation changes versus the other modes.
- US2. As ops lead, I see the first week demand is likely to exceed capacity, with its probability, and the latest date I can activate co-man for it to matter.
- US3. As marketing lead, I see each region's CAC, LTV:CAC, and Response Evidence Score, plus a recommended spend per region with the reason.
- US4. As sales lead, I enter a candidate account (volume, price, penalty, region, earliest start) and get accept / defer / phase with the revenue and risk impact.
- US5. As any user, I type "what if the distributor doubles its order from month 2?" and get a re-solved plan plus explanation.
- US6. As any user, I accept or reject a recommendation with a reason, and it is logged.
- US7. As any user, I can see how trustworthy the forecasts are (backtest accuracy, interval coverage) before trusting the plan.
- US8. As CEO, I'm warned about surplus capacity (perishable waste) as well as shortfalls.

## 6. Functional requirements

### Data
- **FR-1** Ingest a dataset folder of CSVs matching the data contract (ARCH §3). Produce a validation report: schema errors, missing periods, negative values, referential integrity, date coverage.
- **FR-2** Reconstruct **unconstrained demand** (requested qty, including waitlisted and stockout-lost) separately from fulfilled sales.
- **FR-3** Version every ingested dataset by content hash.

### Metrics
- **FR-4** Compute all funnel metrics in IDEATION §7.2 per region × channel × period.
- **FR-5** Compute Response Evidence Score (RES) per region × channel and Account Quality Score (AQS) per B2B account/candidate, with component breakdowns and confidence.

### Forecasting
- **FR-6** Weekly forecasts, 13-week horizon, P10/P50/P90 (plus full sample paths for simulation), for D2C per region and B2B per account/region.
- **FR-7** Rolling-origin backtest harness. Report MASE, pinball loss, and interval coverage per series and model.
- **FR-8** Automatic model selection per series via backtest. Fall back to the seasonal-naive baseline if no model beats it.
- **FR-9** Detect anomalous spikes (e.g., viral-driven). Flag them to the user and prevent them from being extrapolated.
- **FR-10** Forecast output must be independent of strategy mode (hash-equal across modes).

### Capacity
- **FR-11** Probabilistic in-house capacity from batch history (yield distribution, failure rate).
- **FR-12** Co-man capacity from contract terms (lead time, min/max, cost, availability date).
- **FR-13** Configurable perishability (shelf life → carryover rules).

### Allocation and spend
- **FR-14** Solve the allocation (D2C per region, B2B per account) plus marketing spend per region for the horizon under the selected mode.
- **FR-15** Strategy modes configured in YAML (weights, quantiles, floors, gates, thresholds). No code change needed to tune.
- **FR-16** Evidence gate: D2C spend expansion only where RES ≥ mode threshold; capped exploration budget elsewhere.
- **FR-17** Concentration cap per B2B account.
- **FR-18** Monte Carlo stress test of every plan: expected revenue, expected fill rate, P(any B2B shortfall), expected waste.
- **FR-19** Compare against rule-based baselines: proportional, B2B-first, first-come-first-served.
- **FR-20** Side-by-side comparison of modes on the same data.

### Onboarding
- **FR-21** Simulate a candidate account across start-date options and ramp profiles. Output recommendation (accept now / accept from month X / phased ramp / decline), with deltas in revenue, margin, fill rate, D2C displacement, and shortfall probability.

### Alerts and mitigation
- **FR-22** Weekly breach detection: P(demand > capacity) against the mode threshold. Report first breach week, probability, and expected shortfall.
- **FR-23** Mitigation catalog (IDEATION §10) with lead times and costs in config. Offer only mitigations feasible before the breach.
- **FR-24** Rank mitigations by quantified impact (re-solve with the lever enabled). Show act-by date, cost, harm, and reversibility.
- **FR-25** Surplus detection and recommendations.

### AI layer
- **FR-26** Narrative brief generated from the structured run payload only.
- **FR-27** Number-grounding validator. Every number in the narrative must match a payload value (within rounding). On failure, regenerate once, then fall back to a template.
- **FR-28** Natural-language what-if → validated `ScenarioSpec` JSON → re-solve → explained diff. Reject out-of-schema requests with a clear message.
- **FR-29** Provider-agnostic LLM client (default: Anthropic Claude API).

### Decision log
- **FR-30** Store every run (data hash, config hash, git SHA, mode, outputs) and every user accept/reject with reason.
- **FR-31** On a new data drop, compare prior recommendations with realized outcomes and feed the results into RES/AQS refresh (closed loop).

### Evaluation
- **FR-32** Evaluation runner over all blind worlds, producing a frozen report before any unblinding.

## 7. Non-functional requirements

- **NFR-1 Performance:** a full run (forecast + capacity + solve + stress test) completes in under 60 s for up to 20 regions, 50 accounts, 13 weeks on a laptop. A single re-solve for a what-if completes in under 5 s.
- **NFR-2 Determinism:** same data + config + seed → identical outputs.
- **NFR-3 Traceability:** every displayed number links to its run and source table.
- **NFR-4 Integrity:** no code path reads anything outside `data/incoming/` for data. No DGP artifacts in the repo.
- **NFR-5 Testability:** invariant tests for the optimizer (capacity never exceeded, allocation never exceeds demand, floors respected when feasible, mode-invariance of forecast).
- **NFR-6 Secrets:** LLM API key from environment only; never logged.
- **NFR-7 Explainability:** every recommendation has a machine-readable reason list (binding constraints, scores, drivers).

## 8. Screens (v1)

1. **Overview**: demand fan chart (P10–P90) vs. capacity fan chart, breach/surplus markers, mode selector, headline KPIs, narrative brief.
2. **Allocation plan**: month × channel × region table, spend per region, mode comparison, baseline comparison, stress-test results.
3. **Markets**: region scorecards (visitors, bounce, CPL, conversion, CAC, LTV, LTV:CAC, churn, NPS, RES with breakdown), recommended spend and reason.
4. **B2B accounts**: account table with AQS; onboarding simulator form and results.
5. **Alerts & mitigations**: breach/surplus cards with ranked mitigations and act-by dates.
6. **Scenarios**: natural-language what-if box, structured spec preview, before/after diff.
7. **Decision log**: runs, recommendations, accept/reject, outcomes.
8. **Data & model health**: validation report, backtest metrics, coverage, flagged anomalies.

## 9. Success metrics (evaluated across blind worlds)

| Area | Metric | Target |
|---|---|---|
| Forecast | MASE vs. seasonal naive | selected model beats baseline on ≥ 70% of series |
| Forecast | P10–P90 empirical coverage | 70–90% (nominal 80%) |
| Allocation | Realized revenue vs. best rule baseline (on held-out actuals) | higher in ≥ 75% of worlds |
| Allocation | B2B fill rate under STABILITY | ≥ baseline B2B-first in every world |
| Alerts | Breaches flagged ≥ co-man lead time in advance | ≥ 70% of realized breaches |
| Alerts | False-alarm rate | reported, target < 30% |
| AI | Narrative numbers grounded | 100% (validator-enforced) |
| System | NFR-1 latency | met |

Targets are initial and may be revised **only before** evaluation runs. Revisions are logged in NOTES.md.

## 10. Release plan

- **v0.1 Core pipeline:** ingest → metrics → forecast → capacity → allocate (no spend co-optimization) → alerts → CLI output.
- **v0.2 Decision tools:** spend co-optimization, onboarding simulator, mitigation ranking, API.
- **v0.3 Product:** frontend, AI narrative and scenarios, decision log.
- **v1.0 Evaluation:** blind-world evaluation report, demo, polish.
- **Stretch:** interactive replay, where the DGP exposes an environment API that reacts to the app's decisions (still API-only, no internals).

## 11. Risks

| Risk | Mitigation |
|---|---|
| Circular validation | Separate DGP by a different AI, blind worlds, held-out evaluation (IDEATION §11) |
| Spend→demand confounding | Adstock + saturation fit with extrapolation guard; exploration budget as geo-experiments; causal caveat shown in UI |
| Overfitting to synthetic quirks | Multiple worlds; baseline comparisons; simple models preferred when tied |
| LLM hallucinated numbers | Number-grounding validator + template fallback |
| Scope creep | Non-goals list; TASK order; stretch items gated |
| Infeasible LP under tight capacity | Soft constraints with explicit penalty slacks; infeasibility diagnostics reported |

## 12. Assumptions and open questions

See NOTES.md → Assumptions register and Open questions.

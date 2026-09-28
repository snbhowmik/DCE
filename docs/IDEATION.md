# IDEATION — Biokraft Demand-Capacity Engine (DCE)

> **Read this first.** This document explains the *business problem* and the *reasoning* behind every design choice. PRD says what to build, ARCH says how, TASK says in what order. If an implementation choice ever conflicts with a principle in §6 of this document, the principle wins. Log the conflict in NOTES.md and stop.

---

## 1. The company

**Biokraft Foods Private Limited** is an alternative-protein company. Its R&D pipeline includes seaweed-based products, microbial proteins (fermentation-derived), and mycelium-based products. Right now only **one product line is commercial: cultivated chicken.**

It sells through two channels:

- **D2C (direct-to-consumer)** through its own e-commerce, to individual buyers in multiple regions.
- **B2B (wholesale)** to business accounts such as restaurants, QSR chains, hotels, caterers, and distributors, usually under agreements with committed volumes.

Output comes from two sources:

- **In-house bioreactor production**, which runs in batches. Batch yield is variable, and batches can fail.
- **Co-manufacturing (co-man)**, which is extra capacity from partner facilities. It comes with lead times, minimum commitments, and a cost premium.

The total is a **hard ceiling.** In a given month Biokraft cannot simply "make more": no overtime shift doubles a bioreactor's output.

---

## 2. The three coupled decisions

Every planning cycle, Biokraft makes three recurring decisions:

| # | Decision | Owner today | Question |
|---|---|---|---|
| D1 | **Capacity allocation** | Ops / production | How many kg go to D2C vs. B2B next month, and to which regions? |
| D2 | **Marketing geography** | Growth / marketing | Where should next period's marketing budget go? |
| D3 | **B2B onboarding** | Sales / founders | Should we accept, defer, or phase in this new wholesale account? |

**The core insight: these are not three decisions. They are one decision seen from three desks.** They share one pool of capacity:

- Marketing spend in a region *creates* D2C demand, which consumes capacity (with a lag).
- A new B2B account *locks* capacity for months, which starves D2C growth.
- If capacity runs short, marketing spend in that region is *wasted*: you paid to acquire customers you cannot serve. Stockouts then damage satisfaction (NPS), which reduces word-of-mouth and raises future acquisition cost.

So a success in one team can become a failure in another. Marketing hits its ROAS target and creates demand that ops cannot fulfill. Sales signs a big account and marketing's D2C cohort goes unserved. Each team is locally rational, and the company is globally worse off.

---

## 3. Why the traditional approach (spreadsheets) structurally fails

This is not a competence problem. The decision has properties a spreadsheet cannot hold:

1. **It's a constrained optimization, not a lookup.** "Best allocation under a hard ceiling, contract penalties, service floors, and a marketing budget" is a mathematical program. Nobody solves one live in Excel, so the "answer" is really gut feel formatted as a table.
2. **The inputs are uncertain, but the spreadsheet cell is not.** Demand is uncertain. At Biokraft *capacity is also uncertain* (batch yield, failures, co-man timing). A spreadsheet forces one number per cell, so the plan is built against one guess of the future and gives no warning of how wrong it might be.
3. **The data is fragmented and stale.** Orders live in the e-commerce backend and a B2B CRM. Capacity lives with production and procurement. Marketing performance lives in ad platforms. Someone stitches a snapshot together periodically, and it is stale by the time it's done. Asking a new question ("what if we take this account?") is expensive, so people stop asking.
4. **There is no memory.** Decisions are not logged against outcomes, so the company never learns which regions, campaigns, or accounts actually paid off. The same mistakes repeat.
5. **The coupling is invisible.** Because D1, D2, and D3 sit in different sheets owned by different people, nobody sees the tradeoff between them until something breaks.

---

## 4. Why existing software doesn't solve it for Biokraft

The category exists: **Integrated Business Planning (IBP) / Sales & Operations Planning (S&OP)** software (SAP IBP, Kinaxis, o9, Blue Yonder, Anaplan). It doesn't fit here because:

- **Cost and maturity.** These tools assume ERP maturity, dedicated planners, years of clean multi-SKU history, and large budgets. An early-stage deep-tech manufacturer is not the target customer.
- **They treat capacity as known.** Their customers have mature manufacturing. Biokraft's capacity is a *forecasted, uncertain quantity*.
- **They assume flex.** Big CPG firms can flex capacity through extra lines and suppliers. Biokraft's ceiling is closer to hospital ICU beds than to a factory schedule.
- **They weren't designed around a young company's asymmetric bets.** One B2B account can be 30%+ of all output. Onboarding is a strategic, hard-to-reverse bet, not a routine allocation.

**The gap is a lightweight, honest, uncertainty-aware planner built for capacity-starved, early-stage food and bio manufacturers.**

---

## 5. What is actually novel here

"Forecast demand, optimize allocation" is textbook operations research and is not novel on its own. The novelty is what we attach to it for this kind of company:

1. **Two-sided uncertainty.** Demand *and* capacity are both forecast with uncertainty bands, and both feed the same allocation engine.
2. **Perishability.** Cultivated chicken has a limited shelf life, so capacity is largely *use-it-or-lose-it in time*. You can't build much stock ahead of a spike. Surplus is waste, not inventory. Surplus is therefore a flagged risk too, not only shortfall.
3. **Joint spend and capacity optimization.** Marketing spend is a *decision variable* inside the same optimizer as allocation, because spend creates demand that must then be served. D2 and D1 are solved together.
4. **Evidence-gated D2C expansion.** The system expands D2C in a region only where history shows the region *actually responds* to spend in a sustained way. Where evidence is thin, it allocates a small, capped exploration budget to *learn* rather than bet.
5. **Onboarding as a simulated strategic decision.** A candidate B2B account is evaluated by simulating the whole horizon with and without it, across start dates and ramp profiles. The output is not just a yes/no but *when* and *how fast*.
6. **Product pipeline as a planning event.** When an R&D line (seaweed, microbial, mycelium) graduates, it enters the **product matrix** with its own eligibility and economics, and the planner re-solves.
7. **Closed loop.** Every recommendation is logged. Outcomes flow back into evidence scores next cycle. The system learns which of its own recommendations worked.

---

## 6. Core principles (non-negotiable)

**P1. The forecast is descriptive. Strategy lives only in allocation.**
The forecast answers "what will happen if we keep behaving as we have?" and nothing else. It must not know the quarter's strategic focus. Strategy (growth, stability, D2C expansion) changes only the optimizer's objective weights, constraints, gates, and risk quantiles. *Enforced by test: switching strategy mode must produce a byte-identical forecast.*

**P2. Data independence.**
The historical data is produced by a **Data Generating Process (DGP)** built by a *different AI system (Gemini CLI)* in a *separate repository*. This app sees only the published **data contract** (schemas plus field meanings). App developers, human or AI, must never read the DGP's code, parameters, prompts, or scenario descriptions. The reason: if the same mind writes the data and the forecaster, the forecaster is just recovering its author's own assumptions, and any accuracy claim is circular.

**P3. Uncertainty is first-class.**
Every forecast is a distribution, reported at minimum as P10 / P50 / P90. Plans are solved at a strategy-dependent quantile and then stress-tested by Monte Carlo. No single-number forecasts reach the user without their band.

**P4. Numbers come from models. The LLM explains and translates.**
The LLM turns solver output into plain language and turns plain-language what-ifs into structured scenario specs. It never produces, adjusts, or "estimates" a number. Every number in a narrative must trace to a field in the solver payload, and a validator enforces this.

**P5. Everything is traceable and logged.**
Every run records its data hash, config hash, code version, and mode. Every recommendation has a reason trail. Every accepted or rejected recommendation is stored with the human's reason.

**P6. Honest claims only.**
We claim: *the pipeline correctly adapts forecasts, allocations, and mitigations across multiple independently generated demand/capacity regimes, validated on held-out periods.* We do **not** claim: *this predicts Biokraft's real sales.* Moving to production means swapping the data source; the same pipeline then calibrates on real history.

---

## 7. The marketing funnel as business logic

Demand is not a curve. It is the output of a funnel. The app **measures** these quantities from data. It never assumes their values.

### 7.1 The chain

```
Spend ──► Visibility ──► Engagement ──► Lead ──► Qualified lead ──► Customer ──► Repeat / retained ──► Advocate
 (PPC,      (unique        (1 − bounce)   (CTA,    (persona fit)      (conversion)  (1 − churn, LTV)    (NPS, word
  native)    visitors)                     CRO)                                                          of mouth)
```

Movement through **lifecycle stages (awareness → evaluation → purchase)** takes time. So **spend today shows up as orders weeks later.** This lag is the most important structural fact for forecasting and for mitigation timing.

### 7.2 Term-by-term: meaning, formula, and use in the app

| Term | Meaning | Formula (as computed by the app) | Where the app uses it |
|---|---|---|---|
| **Unique visitors** | Distinct people reaching the store/landing page | count distinct visitors / period | Top-of-funnel volume; leading indicator of D2C demand |
| **Bounce rate** | Share who leave without engaging | bounces ÷ unique visitors | Funnel-health metric; a rising bounce rate with flat spend signals message/traffic mismatch |
| **Pay-per-click (PPC)** | Paid ads billed per click | CPC = spend ÷ clicks | Campaign-type efficiency by region |
| **Native advertising** | Paid content that blends with the platform | tracked as a campaign type | Campaign-type comparison; tends to give steadier, spend-proportional traffic |
| **Call to action (CTA)** | The prompt that turns a visitor into a lead | not measured directly | Appears as campaign-level differences in visitor→lead rate; the app treats this as a campaign effect and doesn't design CTAs |
| **Conversion rate optimization (CRO)** | Improving funnel conversion through execution | not measured directly | Same as CTA: visible as conversion-rate shifts over time |
| **Cost per lead (CPL)** | Spend to generate one lead | spend ÷ leads | Region/campaign efficiency |
| **Buyer persona** | Target customer profile | segment label on customers/leads | Segmenting conversion and LTV; B2B account-type priors |
| **Qualified lead** | A lead matching the persona / likely to buy | qualified ÷ leads | Funnel quality; B2B pipeline strength |
| **Conversion rate** | Share who become paying customers | visitor→lead = leads ÷ visitors; lead→customer = conversions ÷ leads | Funnel efficiency by region and channel |
| **Customer acquisition cost (CAC)** | Cost to win one customer | (marketing spend + attributable sales cost) ÷ new customers | Core cost input to spend optimization |
| **Churn rate** | Share of customers lost per period | customers lost ÷ customers at start | Drives repeat demand and LTV |
| **Lifetime value (LTV)** | Margin a customer produces over their life | D2C: AOV × purchase frequency × gross margin × expected lifetime (≈ 1 ÷ churn). B2B: monthly contract margin × expected tenure | Unit economics; account quality |
| **LTV:CAC** | Value created per rupee of acquisition | LTV ÷ CAC | Key health signal. Rule of thumb: > 3 healthy, 1–3 marginal, < 1 destroys value. Used in the evidence gate |
| **Monthly recurring revenue (MRR)** | Predictable monthly revenue | Σ recurring B2B contract revenue + D2C subscriptions (if any) | Revenue stability; favored under the stability strategy |
| **Net promoter score (NPS)** | Willingness to recommend | % promoters (9–10) − % detractors (0–6) | Satisfaction signal; watched after shortfalls |
| **Word of mouth / crowdsourced content** | Organic demand not bought with spend | estimated as the organic (non-spend) component of demand | Baseline demand; the payoff of reliable fulfillment |
| **Viral content** | Rare, large, spend-independent demand spikes | detected as anomalies | Detect and flag; **never extrapolate a spike into the forecast** |
| **Lifecycle stages** | Awareness → evaluation → purchase | lag structure between spend and orders | Response-model lag (adstock); mitigation timing |
| **Customer relationship management (CRM)** | System of record for customers and accounts | the customers and accounts tables | B2B account history; cohort analysis |
| **Closed-loop marketing** | Outcomes feed back into targeting | recommendation → outcome → updated scores | The decision log and evidence-score refresh |
| **Return on investment (ROI)** | Return per rupee spent | (attributed **gross margin** − spend) ÷ spend | Post-hoc campaign/region scorecard. Use margin, not revenue |
| **Product matrix** | Which products are sold where, and their lifecycle status | SKU × channel × region eligibility + status (R&D / pilot / production) | Hard eligibility constraints in the optimizer; pipeline events |

**Caution on causality.** Marketing data is observational: spend is often *raised because* demand was already rising. Naive spend→demand fits overstate marketing's effect. The response model must guard against this (see ARCH), and the exploration budget should run as geo-experiments to generate cleaner evidence.

---

## 8. Strategy modes (the quarter's focus)

Leadership sets a focus for the quarter. The focus changes **how the optimizer decides**, never what the forecast says.

### Mode A — GROWTH (revenue and reach)
- **Goal:** maximize revenue and brand reach.
- **B2B:** favors accounts with **stable volume and wide reach**, such as distributors or chains with many outlets or regions. Onboarding is liberal for accounts with a high Account Quality Score weighted toward reach and volume.
- **D2C:** gets remaining capacity where margin and evidence justify it.
- **Risk posture:** plans against the median (P50) capacity.

### Mode B — STABILITY (fulfillment reliability)
- **Goal:** maximize fill rate and minimize shortfall risk.
- **B2B:** existing commitments are protected with high service floors. Onboarding only for accounts with strong reliability (volume consistency, reorder history, low concentration risk).
- **D2C:** capped earlier, waitlisted earlier.
- **Risk posture:** plans against conservative capacity (≈ P10–P20) and high demand quantiles for commitments.

### Mode C — D2C EXPANSION (risk-on)
- **Goal:** grow D2C share and the customer base.
- **Gate:** extra capacity and spend go to D2C **only in region-channels whose Response Evidence Score passes the threshold**, meaning places where past spend produced *sustained* demand and healthy LTV:CAC, not a spike that died.
- **Exploration:** regions below the gate get a small, capped exploration budget, run as experiments, to gather evidence.
- **B2B:** new onboarding paused unless terms are exceptional.
- **Risk posture:** plans against P50 capacity with a tolerance for D2C waitlisting.

### Mode D — CUSTOM
The user sets weights directly.

| Lever the mode changes | GROWTH | STABILITY | D2C EXPANSION |
|---|---|---|---|
| Objective emphasis | revenue + reach | fill rate / low shortfall | D2C margin + customer growth |
| Capacity quantile planned against | P50 | P10–P20 | P50 |
| B2B service floor | moderate | high | moderate (existing only) |
| Onboarding gate | AQS (reach-weighted) | AQS (reliability-weighted), strict | paused unless exceptional |
| D2C spend expansion | where ROI positive | minimal | where RES ≥ gate, plus capped exploration |
| Breach alert sensitivity | medium | high | medium |

---

## 9. Evidence scores

### 9.1 Response Evidence Score (RES), per region × channel
Answers: *does this market respond to investment in a way that lasts?*

Components:
- **Sustained lift.** After past spend increases, did demand stay elevated or decay back to baseline?
- **Unit economics.** LTV:CAC.
- **Retention.** Repeat rate and 1 − churn.
- **Satisfaction.** NPS.
- **Confidence.** Amount of data. Scores built on few observations are shrunk toward the all-region average (empirical-Bayes style), so a lucky month doesn't look like evidence.

### 9.2 Account Quality Score (AQS), per B2B account or candidate
Answers: *is this account worth the capacity it locks up?*

Components: committed volume, volume stability (low variability of actual orders), reach (outlets/regions served), contract margin, reorder/payment reliability where available, shortfall-penalty exposure, and **concentration risk** (share of total capacity one account would hold). New candidates with no history get priors from similar account types, clearly flagged as priors.

**Concentration rule:** no single account may exceed a configurable share of capacity. For a young company, one account's churn must not be existential.

---

## 10. Mitigation playbook

We do not invent a "history of how Biokraft survived shortages"; there is none to learn from. Mitigations come from **established operations and revenue-management practice**, applied through the system's forecasts and scores.

**Proactive means:** detect the breach *earlier than the lead time of the slowest useful mitigation*. A mitigation is only offered if it can take effect before the breach.

| ID | Mitigation | Typical lead time | Reversible | Cost / harm |
|---|---|---|---|---|
| M1 | **D2C waitlist / pre-order** | days | yes | goodwill cost, low cash cost |
| M2 | **Spend throttle / reallocation**: cut acquisition spend where capacity is short, move it to regions with slack and good RES | days to act, weeks to take effect (funnel lag) | yes | lost future growth in throttled region |
| M3 | **Co-man activation** | weeks (contract lead time) | partly (minimum commitments) | cost premium |
| M4 | **B2B onboarding deferral or phased ramp** | immediate | yes | relationship risk, delayed revenue |
| M5 | **Delivery-slot / promise-date spreading** within shelf life | days | yes | customer convenience |
| M6 | **Contract-aware rebalancing**: partial fills routed to lowest-penalty commitments | immediate | yes | explicit penalty cost; last resort |

Each alert includes: breach week, probability, size, mitigations ranked by (shortfall reduced ÷ cost) with harm and reversibility shown, and an **act-by date** for each.

**Surplus is also flagged.** Because of perishability, unused capacity is waste. Surplus alerts recommend spend increases in high-RES regions or accelerated B2B onboarding.

---

## 11. Data independence protocol

```
┌─────────────────────────────┐   CSV drops only   ┌─────────────────────────────┐
│  biokraft-dgp  (Gemini CLI) │ ─────────────────► │  biokraft-dce (Claude Code) │
│  hidden equations, params,  │  per data contract │  sees columns + meanings    │
│  world definitions          │                    │  never sees how data is made│
└─────────────────────────────┘                    └─────────────────────────────┘
```

Rules:
1. The DGP emits CSVs that conform exactly to the contract in ARCH §3.
2. **No ground-truth labels in the data.** No "viral_event" flag, no true elasticity column, no true demand. The app must infer these, as it would in reality.
3. **Worlds are delivered blind** (`world_01`, `world_02`, …) without descriptions. The DGP side keeps a sealed manifest describing each world.
4. Unblinding happens only after the app's evaluation results for all worlds are frozen and logged in NOTES.md.
5. If an app developer (human or AI) accidentally sees DGP internals, it is logged as an **integrity event** in NOTES.md and that world is excluded from headline results.
6. Contract changes are negotiated through the contract file and a version bump, never by reading the DGP code.

**Why multiple worlds:** we are not searching for the dataset that makes us look good. We are testing whether the *method* behaves sensibly across genuinely different regimes: strong seasonality, high or low spend sensitivity, capacity shocks, lumpy B2B, and flat organic growth.

---

## 12. What we will and won't say

| We say | We don't say |
|---|---|
| "Across N independently generated regimes, the forecaster beat a seasonal-naive baseline on X% of series, with calibrated intervals." | "We predict Biokraft's sales." |
| "The allocator outperformed rule-based baselines (proportional, B2B-first, first-come-first-served) on revenue and fill rate in these regimes." | "This allocation is optimal for Biokraft." |
| "Breaches were flagged a median of K weeks ahead, which exceeds co-man lead time." | "We prevent all stockouts." |
| "With real data, the same pipeline recalibrates." | "No real data needed." |

---

## 13. Internal glossary

- **P10 / P50 / P90**: forecast quantiles. There is a 10% / 50% / 90% chance the true value is below them.
- **Breach**: a future week where P(demand > capacity) exceeds the mode's threshold.
- **Fill rate**: fulfilled ÷ requested quantity.
- **Unconstrained demand**: what customers *asked for*, including waitlisted and stockout-lost orders, as opposed to what was *sold*. Forecast this, not sales.
- **Adstock**: carry-over effect of past spend on current demand (captures funnel lag).
- **Deterministic equivalent**: solving the plan at fixed quantiles, then stress-testing it by sampling.
- **World**: one independently generated synthetic history from the DGP.
- **Run**: one execution of forecast → capacity → allocate → alerts on one dataset with one config.

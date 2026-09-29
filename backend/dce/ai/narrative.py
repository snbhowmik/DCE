"""Narrative brief from a run payload (ARCH §5.11, PRD FR-26/27, D-049/D-050/D-053).

1. `digest(payload)` turns the payload into ~20 short facts with numbers already formatted for
   display. Small models get this compact digest, never the full payload.
2. The LLM writes HEADLINE / FINDINGS / ACTIONS in plain text (small models are unreliable at
   JSON). The NumberGroundingValidator checks the text against the full payload.
3. On a violation: one retry listing the violations; then the deterministic template brief.
   With no LLM configured the template *is* the product, not a degraded mode.
Briefs are cached per run, so a free-tier key is spent at most twice per run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from dce.ai.grounding import NumberGroundingValidator, Violation
from dce.ai.llm import LLMClient, LLMUnavailable, make_client
from dce.config import load_app_config
from dce.store.runs import run_dir

PROMPT_VERSION = "brief-v1"

SYSTEM = """You write a short planning brief for the leadership of Biokraft Foods, a \
capacity-constrained maker of cultivated chicken that sells direct to consumers (D2C) and to \
businesses (B2B).
Rules:
- Use ONLY the facts given. Do not add knowledge, regions, accounts or dates.
- Copy every number exactly as written in the facts, with the same unit and rounding.
- Never calculate, estimate, add, subtract or compare numbers yourself (no "2x", no totals).
- Plain, direct business English. No markdown other than the format below.
Answer in exactly this format:
HEADLINE: <one sentence>
FINDINGS:
- <3 to 5 bullets>
ACTIONS:
- <1 to 3 bullets>"""


# ------------------------------------------------------------------ number formatting


def inr(x: float | None) -> str:
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.1f} L"
    return f"{sign}₹{a:,.0f}"


def kg(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x / 1000:.1f} t" if abs(x) >= 1000 else f"{x:,.0f} kg"


def pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def _mean(d: Any) -> float | None:
    return d.get("mean") if isinstance(d, dict) else d


# ------------------------------------------------------------------ digest


def digest(p: dict[str, Any]) -> list[str]:
    run, k, risk = p["run"], p["kpis"], p["risk"]
    months = run["months"]
    facts = [
        f"World {run['world_id']}, strategy mode {run['mode']}, plan for "
        f"{run['horizon'][0]} to {months[-1]['end']} ({len(months)} months).",
        f"Forecast demand in the plan: {kg(k['demand_kg'])}; planned capacity "
        f"{kg(k['capacity_planned_kg'])} (in-house output at its "
        f"P{round(100 * run['mode_config']['q_capacity'])} level); "
        f"allocated {kg(k['allocated_kg'])}, "
        f"unmet {kg(k['unmet_kg'])}.",
        f"Allocation: D2C {kg(k['d2c_allocated_kg'])}, B2B {kg(k['b2b_allocated_kg'])}.",
        f"Stress test over {p['stress']['n_paths']} simulated futures: expected revenue "
        f"{inr(_mean(k['revenue_inr']))} (P10 {inr(k['revenue_inr']['p10'])}, "
        f"P90 {inr(k['revenue_inr']['p90'])}); expected contribution "
        f"{inr(_mean(k['contribution_inr']))}.",
        f"Expected fill rate: B2B {pct(_mean(k['b2b_fill_rate']))}, D2C "
        f"{pct(_mean(k['d2c_fill_rate']))}; chance that any B2B account is short: "
        f"{pct(k['p_any_b2b_shortfall'])}.",
        f"Expected perishable waste: {kg(_mean(k['waste_kg']))}.",
    ]
    if k["coman_requested_kg"] > 0:
        active = [c for c in p["plan"]["coman"] if c["active"]]
        first = min(c["month"] for c in active) + 1 if active else None
        facts.append(
            f"Co-manufacturing switched on: {kg(k['coman_requested_kg'])} requested, cost "
            f"{inr(k['coman_cost_inr'])}" + (f", starting in month {first}." if first else ".")
        )
    else:
        facts.append("No co-manufacturing is used in this plan.")
    if k["spend_planned_inr"]:
        facts.append(
            f"Marketing spend: planned {inr(k['spend_planned_inr'])}, recommended "
            f"{inr(k['spend_recommended_inr'])}."
        )
    low = [m["region_id"] for m in p["markets"] if (m.get("response") or {}).get("low_confidence")]
    if low:
        facts.append(
            f"Spend response is not identifiable in {len(low)} of {len(p['markets'])} regions, "
            "so their spend is held at plan."
        )
    for a in risk["alerts"][:3]:
        what = "shortfall" if a["kind"] == "breach" else "surplus (perishable waste risk)"
        facts.append(
            f"{a['kind'].upper()} alert: {what} from week of {a['start']} to week of {a['end']}, "
            f"{a['weeks_until']} weeks from now, peak probability {pct(a['peak_probability'])}, "
            f"expected {kg(a['expected_kg'])}."
        )
    if not risk["alerts"]:
        facts.append("No breach or surplus alerts at this mode's thresholds.")
    if k.get("best_baseline"):
        facts.append(
            f"Best rule-based baseline ({k['best_baseline']}): expected revenue "
            f"{inr(k['best_baseline_revenue_inr'])}, waste {kg(k['best_baseline_waste_kg'])}."
        )
    for b in sorted(p["plan"]["binding"], key=lambda b: -abs(b["shadow_price"]))[:2]:
        facts.append(
            f"Binding constraint {b['constraint']}: {b['meaning']} = "
            f"₹{abs(b['shadow_price']):,.0f}."
        )
    h = p["health"]
    if h.get("share_beating_baseline") is not None:
        facts.append(
            f"Forecast health: {pct(h['share_beating_baseline'], 0)} of D2C series beat the "
            f"seasonal-naive baseline; calibrated P10–P90 coverage "
            f"{pct(h.get('coverage_mean_calibrated'), 0)}."
        )
    return facts


# ------------------------------------------------------------------ parsing and template


def parse(text: str) -> dict[str, Any] | None:
    head: str | None = None
    findings: list[str] = []
    actions: list[str] = []
    cur: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("HEADLINE:"):
            head = s.split(":", 1)[1].strip()
        elif up.startswith("FINDINGS"):
            cur = findings
        elif up.startswith("ACTIONS"):
            cur = actions
        elif s[:1] in "-*•" and cur is not None:
            cur.append(s[1:].strip())
    if not head or not findings:
        return None
    return {"headline": head, "findings": findings[:6], "actions": actions[:4]}


def template_brief(p: dict[str, Any]) -> dict[str, Any]:
    k, risk = p["kpis"], p["risk"]
    breach = next((a for a in risk["alerts"] if a["kind"] == "breach"), None)
    surplus = next((a for a in risk["alerts"] if a["kind"] == "surplus"), None)
    if breach:
        head = (
            f"Shortfall risk from the week of {breach['start']}: peak probability "
            f"{pct(breach['peak_probability'])}."
        )
    elif surplus:
        head = (
            f"No shortfall expected; surplus risk from the week of {surplus['start']} "
            f"(expected {kg(surplus['expected_kg'])})."
        )
    else:
        head = (
            f"Plan covers forecast demand; expected B2B fill rate {pct(_mean(k['b2b_fill_rate']))}."
        )
    findings = [
        f"Expected revenue {inr(_mean(k['revenue_inr']))} (P10 {inr(k['revenue_inr']['p10'])}, "
        f"P90 {inr(k['revenue_inr']['p90'])}).",
        f"Allocated {kg(k['allocated_kg'])} of {kg(k['demand_kg'])} forecast demand: "
        f"D2C {kg(k['d2c_allocated_kg'])}, B2B {kg(k['b2b_allocated_kg'])}.",
        f"Chance that any B2B account is short: {pct(k['p_any_b2b_shortfall'])}; expected "
        f"waste {kg(_mean(k['waste_kg']))}.",
    ]
    if k.get("best_baseline"):
        findings.append(
            f"Best rule baseline ({k['best_baseline']}): revenue "
            f"{inr(k['best_baseline_revenue_inr'])}, waste {kg(k['best_baseline_waste_kg'])}."
        )
    actions: list[str] = []
    if k["coman_requested_kg"] > 0:
        actions.append(
            f"Confirm co-manufacturing: {kg(k['coman_requested_kg'])} at "
            f"{inr(k['coman_cost_inr'])}."
        )
    if breach:
        actions.append("Review mitigations before the breach week (Alerts screen).")
    if surplus:
        actions.append("Plan for surplus: promotions or reduced co-man before it spoils.")
    if not actions:
        actions.append("No action needed beyond executing the allocation.")
    return {"headline": head, "findings": findings, "actions": actions}


def _text(b: dict[str, Any]) -> str:
    return "\n".join([b["headline"], *b["findings"], *b["actions"]])


# ------------------------------------------------------------------ generation + cache


def generate(p: dict[str, Any], client: LLMClient | None) -> dict[str, Any]:
    facts = digest(p)
    validator = NumberGroundingValidator(p)
    base: dict[str, Any] = {
        "prompt_version": PROMPT_VERSION,
        "facts": facts,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if client is None:
        return {**base, **template_brief(p), "source": "template", "model": None,
                "fallback_reason": "no LLM configured", "violations": []}  # fmt: skip
    user = f"FACTS for run {p['run']['run_id']}:\n" + "\n".join(f"- {f}" for f in facts)
    violations: list[Violation] = []
    log: list[dict[str, str]] = []  # prompt/response exchanges (T9.1); no headers, no key
    base["system_prompt"] = SYSTEM
    base["log"] = log
    try:
        for attempt in range(2):
            prompt = user
            if attempt and violations:
                bad = ", ".join(sorted({v.token for v in violations}))
                prompt += (
                    f"\n\nYour previous answer used numbers that are not in the facts: {bad}. "
                    "Rewrite it using only numbers copied exactly from the facts."
                )
            answer = client.complete(SYSTEM, prompt)
            log.append({"prompt": prompt, "response": answer})
            brief = parse(answer)
            if brief is None:
                violations = [Violation("<format>", "answer not in the required format")]
                continue
            violations = validator.check(_text(brief))
            if not violations:
                return {**base, **brief, "source": "llm", "model": client.name,
                        "fallback_reason": None, "violations": []}  # fmt: skip
        reason = "LLM answer failed number grounding twice"
    except LLMUnavailable as exc:
        return {**base, **template_brief(p), "source": "template", "model": client.name,
                "fallback_reason": f"LLM unavailable: {exc}", "violations": [],
                "transient": True}  # fmt: skip
    return {
        **base,
        **template_brief(p),
        "source": "template",
        "model": client.name,
        "fallback_reason": reason,
        "violations": [{"token": v.token, "reason": v.reason} for v in violations],
    }


def cached_brief(p: dict[str, Any], client: LLMClient | None = None) -> dict[str, Any]:
    """Brief for a run, cached next to its payload. Regenerated when the model changes or the
    previous attempt failed for a transient reason (network, rate limit)."""
    if client is None:
        client = make_client(load_app_config().get("llm", {}))
    name = client.name if client else None
    path = run_dir(p["run"]["run_id"]) / "narrative.json"
    if path.exists():
        cached = json.loads(path.read_text())
        if (
            cached.get("prompt_version") == PROMPT_VERSION
            and cached.get("model") == name
            and not cached.get("transient")
        ):
            return cached
    brief = generate(p, client)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(brief, ensure_ascii=False, indent=1))
    return brief

""" "Ask the plan": chat over a run (PRD FR-28, US5, T9.3; IDEATION P4).

Each user message is handled in one of two ways:
1. What-if. A deterministic parser turns common phrasings into `Levers` (no LLM needed); the
   scenario is re-solved and the answer compares base and scenario with numbers computed in code.
2. Question. The LLM answers from a compact fact sheet of the run plus the recent conversation.
   Every number in the answer is checked against the run payload; one retry, then a fallback
   that quotes the most relevant facts. The LLM never produces or edits a number.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from dce.ai.grounding import NumberGroundingValidator, normalize
from dce.ai.llm import LLMClient, LLMUnavailable
from dce.ai.narrative import digest, inr, kg, pct
from dce.scenario import Levers

SYSTEM = """You are the planning assistant inside Biokraft's Demand-Capacity Engine, talking to a \
business user about ONE production plan (cultivated chicken; D2C = direct to consumers, B2B = \
wholesale accounts; capacity is a hard ceiling).
Rules:
- Answer only from the FACTS. If the facts do not contain the answer, say so briefly and suggest a
  what-if the user can type (e.g. "what if capacity drops 30% from week 3?").
- Copy numbers exactly as written in the facts. Never calculate, estimate or compare numbers
  yourself.
- When suggesting actions, use only this playbook: D2C waitlist / pre-order, spend throttle or
  reallocation, co-manufacturing, deferring or phasing a new B2B account, delivery-date spreading,
  contract-aware rebalancing (and for surplus: more spend, trimming co-man, D2C promotion).
- Be brief and friendly: at most 5 short sentences or up to 5 "- " bullets. Plain text, no
  tables."""

WORDS_UP = (
    r"(?:up|rise|rises|increase[sd]?|grow[sn]?|jump[sd]?|surge[sd]?|boost(?:ed)?|higher|more)"
)
WORDS_DOWN = (
    r"(?:down|drop[sp]?|dropped|fall[s]?|fell|cut|decrease[sd]?|reduc(?:e|ed|es|tion)|"
    r"lower|shrink[s]?|lose[s]?|less|halve[sd]?)"
)
PCT = r"(\d+(?:\.\d+)?)\s*%"
ACCOUNT_TYPES = ("distributor", "qsr", "hotel", "restaurant", "caterer")


def _signed(text: str, subject: str) -> float | None:
    """'<subject> ... up/down ... N%' or 'N% more/less <subject>' → ±N."""
    for pat, sign in ((WORDS_UP, 1), (WORDS_DOWN, -1)):
        m = re.search(rf"{subject}[^.?!]{{0,40}}?{pat}[^.?!\d]{{0,15}}{PCT}", text)
        if m:
            return sign * float(m.group(1))
        m = re.search(rf"{PCT}\s*{pat}\s+{subject}", text)
        if m:
            return sign * float(m.group(1))
    return None


def _week_of_month(month: int, month_weeks: list[int]) -> int:
    return 1 + sum(month_weeks[: max(0, min(month, len(month_weeks)) - 1)])


def parse_whatif(text: str, p: dict[str, Any]) -> dict[str, Any]:
    """Lever updates found in `text` (empty dict = not a what-if we can execute)."""
    t = normalize(text).lower().replace("co-manufacturing", "co-man").replace("coman", "co-man")
    upd: dict[str, Any] = {}
    month_weeks = [m["n_weeks"] for m in p["run"]["months"]]

    if m := re.search(r"weeks?\s+(\d+)\s*(?:-|to|through|and)\s*(\d+)", t):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        upd |= {
            "capacity_from_week": max(1, a),
            "capacity_to_week": min(13, b),
            "from_week": max(1, a),
        }
    elif m := re.search(r"(?:from|starting|after|in)\s+week\s+(\d+)", t):
        upd |= {
            "from_week": min(13, int(m.group(1))),
            "capacity_from_week": min(13, int(m.group(1))),
        }
    elif m := re.search(r"(?:from|starting|after|in)\s+month\s+(\d)", t):
        w = _week_of_month(int(m.group(1)), month_weeks)
        upd |= {"from_week": w, "capacity_from_week": w}

    if (
        v := _signed(t, r"(?:in-house\s+)?(?:capacity|production|output|supply|yield)")
    ) is not None:
        upd["capacity_pct"] = max(-90.0, min(100.0, v))
    if (v := _signed(t, r"(?:d2c|consumer|online|direct)(?:\s+demand)?")) is not None:
        upd["d2c_demand_pct"] = max(-90.0, min(300.0, v))
    if (v := _signed(t, r"(?:b2b|wholesale)(?:\s+demand)?")) is not None:
        upd["b2b_demand_pct"] = max(-90.0, min(300.0, v))
    if (v := _signed(t, r"(?:marketing\s+)?(?:budget|spend)")) is not None:
        upd["budget_pct"] = max(-100.0, min(200.0, v))
    if re.search(
        r"(?:no|without|lose|lost)\s+(?:the\s+)?co-man"
        r"|co-man\s+(?:is\s+)?(?:unavailable|not available|down|out|fails)",
        t,
    ):
        upd["coman_available"] = False

    # one account: explicit id, or the largest active account of a named type
    acc = None
    for a in p["run"]["accounts"]:
        if a.lower() in t:
            acc = a
    if acc is None:
        for typ in ACCOUNT_TYPES:
            if re.search(rf"\b{typ}", t):
                cands = [
                    a for a in p["accounts"]
                    if a.get("in_plan")
                    and (a.get("aqs") or {}).get("account_type", "").startswith(typ[:5])
                ]  # fmt: skip
                if cands:
                    acc = max(cands, key=lambda a: a.get("committed_kg_per_month") or 0)[
                        "account_id"
                    ]
                    break
    if acc:
        chg = None
        if re.search(r"\bdoubl", t):
            chg = 100.0
        elif re.search(r"\btripl", t):
            chg = 200.0
        elif re.search(r"\bhalv|\bhalf", t):
            chg = -50.0
        elif re.search(r"\b(?:cancel|drops out|leaves|churn|stops ordering)", t):
            chg = -90.0
        else:
            chg = _signed(t, r"(?:orders?|volume|demand)")
        if chg is not None:
            upd |= {"account_id": acc, "account_demand_pct": chg}

    for name, mode in (
        ("d2c expansion", "D2C_EXPANSION"),
        ("growth", "GROWTH"),
        ("stability", "STABILITY"),
    ):
        if re.search(
            rf"(?:switch|change|move|go|use|under|with|in)\s+(?:to\s+)?(?:the\s+)?{name}", t
        ):
            upd["mode"] = mode
            break
    return upd


def _kpis(p: dict[str, Any]) -> dict[str, float]:
    k = p["kpis"]
    return {
        "revenue": k["revenue_inr"]["mean"],
        "contribution": k["contribution_inr"]["mean"],
        "b2b_fill": k["b2b_fill_rate"]["mean"],
        "d2c_fill": k["d2c_fill_rate"]["mean"],
        "p_short": k["p_any_b2b_shortfall"],
        "unmet": k["unmet_kg"],
        "waste": k["waste_kg"]["mean"],
        "coman": k["coman_requested_kg"],
    }


def compare_reply(base: dict[str, Any], scen: dict[str, Any], changes: list[str]) -> str:
    """Deterministic base-vs-scenario summary (every number computed here, in code)."""
    a, b = _kpis(base), _kpis(scen)

    def pp(x: float) -> str:
        return "no change" if abs(x) < 0.0005 else f"{100 * x:+.1f} pp"

    lines = [f"I re-solved the plan with: {'; '.join(changes) or 'the same levers'}."]
    lines.append(
        f"- Expected revenue {inr(a['revenue'])} → {inr(b['revenue'])}; "
        f"contribution {inr(a['contribution'])} → {inr(b['contribution'])}."
    )
    lines.append(
        f"- B2B fill {pct(a['b2b_fill'])} → {pct(b['b2b_fill'])} "
        f"({pp(b['b2b_fill'] - a['b2b_fill'])}), "
        f"D2C fill {pct(a['d2c_fill'])} → {pct(b['d2c_fill'])} "
        f"({pp(b['d2c_fill'] - a['d2c_fill'])})."
    )
    lines.append(
        f"- Chance any B2B account is short {pct(a['p_short'])} → {pct(b['p_short'])}; "
        f"unmet in plan {kg(a['unmet'])} → {kg(b['unmet'])}; "
        f"waste {kg(a['waste'])} → {kg(b['waste'])}."
    )
    if a["coman"] or b["coman"]:
        lines.append(f"- Co-manufacturing {kg(a['coman'])} → {kg(b['coman'])}.")
    alerts = scen["risk"]["alerts"]
    if alerts:
        x = alerts[0]
        what = "Shortfall" if x["kind"] == "breach" else "Surplus"
        lines.append(
            f"- First alert: {what.lower()} from the week of {x['start']}, peak probability "
            f"{pct(x['peak_probability'])}, expected {kg(x['expected_kg'])}."
        )
    else:
        lines.append("- No shortfall or surplus alerts in this scenario.")
    return "\n".join(lines)


def fact_sheet(p: dict[str, Any]) -> list[str]:
    """Digest plus a few per-line facts a user is likely to ask about."""
    facts = digest(p)
    alloc = p["plan"]["allocation"]
    for acc in p["run"]["accounts"]:
        rows = [r for r in alloc if r["account_id"] == acc]
        if rows:
            facts.append(
                f"B2B account {acc}: allocated {kg(sum(r['allocated_kg'] for r in rows))} of "
                f"{kg(sum(r['demand_kg'] for r in rows))} forecast orders; unmet "
                f"{kg(sum(r['unmet_kg'] for r in rows))}."
            )
    top = sorted((m for m in p["markets"] if m.get("res")), key=lambda m: -(m["res"]["res"] or 0))[
        :3
    ]
    if top:
        facts.append(
            "Regions with the strongest spend-response evidence (RES): "
            + ", ".join(f"{m['region_id']} {m['res']['res']:.2f}" for m in top)
            + "."
        )
    return facts


def _relevant(facts: list[str], question: str, n: int = 4) -> list[str]:
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 3}
    scored = sorted(facts, key=lambda f: -len(words & set(re.findall(r"[a-z0-9]+", f.lower()))))
    return scored[:n]


SUPPORTED = (
    "capacity up/down N% (optionally 'weeks 3-6' or 'from week 5'), D2C or B2B demand up/down N%, "
    "an account (id, or 'the distributor') doubling / halving / +N%, 'no co-man', marketing budget "
    "up/down N%, or 'switch to growth / stability / D2C expansion'"
)


def reply(
    p: dict[str, Any],
    messages: list[dict[str, str]],
    client: LLMClient | None,
    run_whatif: Callable[[Levers], tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """One assistant turn. `run_whatif(levers) -> (scenario_meta, scenario_payload)`."""
    question = messages[-1]["content"].strip()
    upd = parse_whatif(question, p)
    if upd:
        base_levers = Levers(**(p["run"].get("levers") or {"mode": p["run"]["mode"]}))
        lv = base_levers.model_copy(update=upd)
        lv = Levers.model_validate(lv.model_dump())  # re-validate ranges
        meta, sp = run_whatif(lv)
        return {
            "reply": compare_reply(p, sp, meta["changes"]),
            "source": "rules",
            "model": None,
            "scenario": meta,
        }
    if re.search(r"\bwhat if\b|\bsuppose\b|\bimagine\b", question.lower()):
        return {
            "reply": "I couldn't turn that into a scenario I can re-solve. I understand: "
            + SUPPORTED
            + ".",
            "source": "rules",
            "model": None,
        }
    facts = fact_sheet(p)
    fallback = {
        "reply": "Here is what this plan says that looks relevant:\n"
        + "\n".join(f"- {f}" for f in _relevant(facts, question)),
        "source": "facts",
        "model": client.name if client else None,
    }
    if client is None:
        return fallback | {"note": "no LLM configured"}
    validator = NumberGroundingValidator(p)
    history = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages[-7:-1])
    user = (
        f"FACTS for run {p['run']['run_id']}:\n"
        + "\n".join(f"- {f}" for f in facts)
        + (f"\n\nEARLIER IN THIS CHAT:\n{history}" if history else "")
        + f"\n\nQUESTION: {question}"
    )
    bad: list[str] = []
    try:
        for _ in range(2):
            prompt = user + (
                f"\n\nYour previous answer used numbers not in the facts ({', '.join(bad)}). "
                "Answer again using only numbers copied from the facts."
                if bad
                else ""
            )
            ans = normalize(client.complete(SYSTEM, prompt)).strip()
            viol = validator.check(ans)
            if ans and not viol:
                return {"reply": ans, "source": "llm", "model": client.name}
            bad = sorted({v.token for v in viol}) or ["<empty answer>"]
    except LLMUnavailable as exc:
        return fallback | {"note": f"LLM unavailable: {exc}"}
    return fallback | {"note": "the model's answer failed the number check twice"}

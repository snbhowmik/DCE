"""T9.1/T9.2: LLM client, number grounding, narrative with retry + template fallback, cache."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from dce import paths
from dce.ai import llm as llm_mod
from dce.ai.grounding import NumberGroundingValidator
from dce.ai.llm import LLMUnavailable, OpenAICompatibleClient, make_client
from dce.ai.narrative import cached_brief, digest, generate, template_brief


def payload(run_id: str = "run_t") -> dict[str, Any]:
    """Minimal synthetic payload with every field the digest reads (not evaluation data)."""
    q = {"mean": 0.9987, "p10": 0.99, "p50": 1.0, "p90": 1.0}
    return {
        "run": {
            "run_id": run_id,
            "world_id": "tiny",
            "mode": "STABILITY",
            "horizon": ["2025-01-06", "2025-01-13"],
            "months": [{"idx": 0, "start": "2025-01-06", "end": "2025-01-19", "n_weeks": 2}],
            "mode_config": {"q_capacity": 0.15},
        },
        "kpis": {
            "demand_kg": 34665.2,
            "capacity_planned_kg": 39605.8,
            "allocated_kg": 34600.0,
            "unmet_kg": 65.2,
            "d2c_allocated_kg": 10448.8,
            "b2b_allocated_kg": 24151.2,
            "revenue_inr": {"mean": 23151104.6, "p10": 22741812.1, "p50": 2.3e7, "p90": 23540451.5},
            "contribution_inr": {"mean": -9056869.0, "p10": 0, "p50": 0, "p90": 0},
            "b2b_fill_rate": q,
            "d2c_fill_rate": q,
            "p_any_b2b_shortfall": 0.034,
            "waste_kg": {"mean": 4321.1, "p10": 0, "p50": 0, "p90": 0},
            "coman_requested_kg": 2700.0,
            "coman_cost_inr": 1377000.0,
            "spend_planned_inr": 0.0,
            "spend_recommended_inr": 0.0,
            "best_baseline": "b2b_first",
            "best_baseline_revenue_inr": 23149000.0,
            "best_baseline_waste_kg": 1945.8,
        },
        "stress": {"n_paths": 500},
        "plan": {
            "coman": [{"coman_id": "COMAN-PUN-01", "month": 1, "active": True}],
            "binding": [{"constraint": "capacity_m0", "meaning": "value of +1 kg", "shadow_price": -390.0}],
        },
        "risk": {
            "alerts": [
                {"kind": "surplus", "start": "2025-01-13", "end": "2025-01-13", "weeks_until": 1,
                 "peak_probability": 0.76, "expected_kg": 5820.4}
            ]
        },
        "markets": [{"region_id": "r1", "response": {"low_confidence": True}}],
        "health": {"share_beating_baseline": 1.0, "coverage_mean_calibrated": 0.8433},
    }  # fmt: skip


class FakeClient:
    name = "fake-8b@local"

    def __init__(self, answers: list[str | Exception]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


GOOD = """HEADLINE: Surplus risk from the week of 2025-01-13, peak probability 76.0%.
FINDINGS:
- Expected revenue ₹2.32 Cr with B2B fill rate 99.9%.
- Co-manufacturing of 2.7 t costs ₹13.8 L.
- Expected waste 4.3 t.
ACTIONS:
- Review co-man volume for ACC-REST-502 before month 2."""


# ------------------------------------------------------------------ grounding


def test_validator_accepts_display_formats_and_skips_identifiers() -> None:
    v = NumberGroundingValidator(payload())
    assert v.check(GOOD) == []
    assert v.check("P90 revenue ₹2.35 Cr; world_06; COMAN-PUN-01; 34,665 kg; 3.4%") == []


def test_validator_rejects_injected_and_derived_numbers() -> None:
    v = NumberGroundingValidator(payload())
    bad = v.check("Revenue ₹2.95 Cr, waste 2.2× the baseline, shortfall 17%, from 2025-02-03.")
    assert {x.token for x in bad} == {"2.95 Cr", "2.2", "17%", "2025-02-03"}


def test_digest_and_template_are_grounded() -> None:
    p = payload()
    v = NumberGroundingValidator(p)
    assert v.check("\n".join(digest(p))) == []
    t = template_brief(p)
    assert v.check("\n".join([t["headline"], *t["findings"], *t["actions"]])) == []


# ------------------------------------------------------------------ generation


def test_llm_brief_used_when_grounded() -> None:
    out = generate(payload(), FakeClient([GOOD]))
    assert out["source"] == "llm" and out["model"] == "fake-8b@local"
    assert out["headline"].startswith("Surplus risk") and len(out["findings"]) == 3


def test_retry_lists_violations_then_succeeds() -> None:
    fake = FakeClient([GOOD.replace("₹2.32 Cr", "₹2.95 Cr"), GOOD])
    out = generate(payload(), fake)
    assert out["source"] == "llm"
    assert "2.95 Cr" in fake.prompts[1] and "not in the facts" in fake.prompts[1]


def test_two_failures_fall_back_to_template() -> None:
    bad = GOOD.replace("4.3 t", "9.9 t")
    out = generate(payload(), FakeClient([bad, bad]))
    assert out["source"] == "template" and out["violations"]
    assert out["fallback_reason"] == "LLM answer failed number grounding twice"


def test_unavailable_llm_and_no_llm_use_template() -> None:
    out = generate(payload(), FakeClient([LLMUnavailable("rate-limited (HTTP 429)")]))
    assert out["source"] == "template" and out["transient"] is True
    assert generate(payload(), None)["fallback_reason"] == "no LLM configured"


def test_unparseable_answer_counts_as_failure() -> None:
    out = generate(payload(), FakeClient(["Sure! Here is a summary.", "still no format"]))
    assert out["source"] == "template"


# ------------------------------------------------------------------ client


def test_make_client_needs_provider_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {"provider": "openai_compatible", "base_url": "https://api.groq.com/openai/v1",
           "model": "llama-3.1-8b-instant", "api_key_env": "GROQ_API_KEY"}  # fmt: skip
    monkeypatch.setattr(llm_mod, "env_value", lambda k: None)
    assert make_client(cfg) is None
    assert make_client({"provider": "none"}) is None
    monkeypatch.setattr(llm_mod, "env_value", lambda k: "gsk_secret")
    c = make_client(cfg)
    assert isinstance(c, OpenAICompatibleClient) and "gsk_secret" not in repr(c)
    with pytest.raises(ValueError):
        make_client({"provider": "anthropic"})


def test_openai_compatible_request_and_rate_limit_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "HEADLINE: ok"}}]})

    c = OpenAICompatibleClient(
        "https://api.groq.com/openai/v1", "llama-3.1-8b-instant", "gsk_x",
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    assert c.complete("sys", "user") == "HEADLINE: ok"
    req = seen[-1]
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer gsk_x"
    body = json.loads(req.content)
    assert body["model"] == "llama-3.1-8b-instant" and body["messages"][0]["role"] == "system"


def test_auth_error_is_not_retried() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401)

    c = OpenAICompatibleClient("http://x/v1", "m", "k", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMUnavailable, match="401"):
        c.complete("s", "u")
    assert len(calls) == 1


# ------------------------------------------------------------------ cache


def test_cache_reuses_and_refreshes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "PROCESSED_DIR", tmp_path)
    p = payload("run_cache")
    fake = FakeClient([GOOD])
    first = cached_brief(p, fake)
    assert first["source"] == "llm"
    assert cached_brief(p, FakeClient([]))["headline"] == first["headline"]  # no call made
    other = FakeClient([GOOD])
    other.name = "other-model@local"
    cached_brief(p, other)
    assert other.prompts  # model changed → regenerated
    flaky = FakeClient([LLMUnavailable("network error")])
    flaky.name = "flaky@local"
    assert cached_brief(p, flaky)["transient"] is True
    retry = FakeClient([GOOD])
    retry.name = "flaky@local"
    assert cached_brief(p, retry)["source"] == "llm"  # transient failure not reused


def test_validator_handles_unicode_dashes_and_month_names() -> None:
    v = NumberGroundingValidator(payload())
    assert v.check("Surplus from week of 2025‑01‑13, peak 76.0%.") == []
    assert v.check("Surplus from 13 Jan 2025 (Jan 13, 2025).") == []
    assert {x.token for x in v.check("Surplus from 20 Jan 2025.")} == {"20 Jan 2025"}


def test_signed_numbers_are_checked_but_ids_are_not() -> None:
    v = NumberGroundingValidator(payload())
    assert {x.token for x in v.check("Waste changes by -684 kg.")} == {"684 kg"}
    assert v.check("Partner COMAN-PUN-01 and ACC-REST-502.") == []

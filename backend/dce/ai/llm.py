"""LLM client (D-049/D-050/D-053): one OpenAI-compatible chat client, or none.

OpenAI-compatible covers Groq, Ollama, llama.cpp server, vLLM, LM Studio and most low-cost hosted
APIs. The key is read from the environment (or the repo's gitignored `.env`) and never logged
(NFR-6). Transport errors and rate limits surface as `LLMUnavailable`; callers fall back to the
template brief, so the product works with no LLM at all.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from dce import paths


class LLMUnavailable(RuntimeError):
    """No usable LLM: not configured, no key, network error, or rate-limited past retries."""


class LLMClient(Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, system: str, user: str) -> str: ...


def env_value(key: str) -> str | None:
    """Environment first, then `<repo>/.env` (KEY=value lines). Values are never logged."""
    if os.environ.get(key):
        return os.environ[key]
    env = paths.ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k.strip() == key and v.strip():
                return v.strip().strip('"').strip("'")
    return None


@dataclass
class OpenAICompatibleClient:
    base_url: str
    model: str
    api_key: str | None = field(repr=False)  # never printed or logged (NFR-6)
    timeout_s: float = 30.0
    max_retries: int = 2
    max_output_tokens: int = 450
    temperature: float = 0.2
    extra: dict[str, Any] = field(default_factory=dict)  # endpoint-specific body fields
    transport: httpx.BaseTransport | None = field(default=None, repr=False)  # tests: mock

    @property
    def name(self) -> str:
        return f"{self.model}@{httpx.URL(self.base_url).host}"

    def complete(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            **self.extra,
        }
        url = self.base_url.rstrip("/") + "/chat/completions"
        last = "no attempt"
        with httpx.Client(timeout=self.timeout_s, transport=self.transport) as http:
            for attempt in range(self.max_retries + 1):
                try:
                    r = http.post(url, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    last = f"network error: {type(exc).__name__}"
                else:
                    if r.status_code == 200:
                        try:
                            return str(r.json()["choices"][0]["message"]["content"] or "")
                        except (KeyError, IndexError, ValueError) as exc:
                            raise LLMUnavailable(f"malformed response: {exc!r}") from exc
                    last = f"HTTP {r.status_code}"
                    if r.status_code in (400, 401, 403, 404):
                        break  # configuration problem: retrying will not help
                    if r.status_code == 429:
                        last = "rate-limited (HTTP 429)"
                        wait = _retry_after(r)
                        if wait is not None and wait <= 10 and attempt < self.max_retries:
                            time.sleep(wait)
                            continue
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 4))
        raise LLMUnavailable(last)


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers.get("retry-after", ""))
    except ValueError:
        return None


def make_client(cfg: dict[str, Any]) -> LLMClient | None:
    """None when the provider is `none` or no key is configured for a keyed endpoint."""
    provider = str(cfg.get("provider", "none"))
    if provider == "none":
        return None
    if provider != "openai_compatible":
        raise ValueError(f"unknown llm.provider {provider!r} (none | openai_compatible)")
    key_env = cfg.get("api_key_env")
    key = env_value(str(key_env)) if key_env else None
    if key_env and not key:
        return None
    return OpenAICompatibleClient(
        base_url=str(cfg["base_url"]),
        model=str(cfg["model"]),
        api_key=key,
        timeout_s=float(cfg.get("timeout_s", 30)),
        max_retries=int(cfg.get("max_retries", 2)),
        max_output_tokens=int(cfg.get("max_output_tokens", 450)),
        temperature=float(cfg.get("temperature", 0.2)),
        extra=dict(cfg.get("extra") or {}),
    )

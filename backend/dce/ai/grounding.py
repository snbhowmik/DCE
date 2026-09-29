"""NumberGroundingValidator (ARCH §5.11, PRD FR-27, IDEATION P4).

Every number in a narrative must match a number in the run payload, within the rounding of the
way it is written: "₹2.32 Cr" matches any payload value in [2.315e7, 2.325e7], "99.9%" matches
0.9985–0.9995 (or 99.85–99.95), "4.3 t" matches 4,250–4,350 kg. ISO dates must appear verbatim in
the payload. Numbers inside identifiers (ACC-REST-502, world_06, P90) are not claims and are
skipped. Derived figures ("2.2× the waste", sums, differences) are rejected by design: the LLM may
copy numbers, never compute them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np

UNITS = {
    "cr": 1e7,
    "crore": 1e7,
    "l": 1e5,
    "lakh": 1e5,
    "m": 1e6,
    "k": 1e3,
    "t": 1e3,
    "tonnes": 1e3,
    "tonne": 1e3,
    "kg": 1.0,
    "%": None,  # handled separately
}
NUM = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<unit>%|crore|Cr|lakh|tonnes|tonne|kg|L|M|k|t)(?![A-Za-z]))?"
)
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
LIST_MARK = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass(frozen=True)
class Violation:
    token: str
    reason: str


def flatten(payload: Any) -> tuple[np.ndarray, set[str]]:
    """All numbers (as a sorted array of |value|) and all strings in a JSON-like structure."""
    nums: list[float] = []
    strs: set[str] = set()
    stack = [payload]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            stack.extend(v.values())
            strs.update(str(k) for k in v)
        elif isinstance(v, list | tuple):
            stack.extend(v)
        elif isinstance(v, bool) or v is None:
            continue
        elif isinstance(v, int | float):
            nums.append(abs(float(v)))
        elif isinstance(v, str):
            strs.add(v)
    return np.unique(np.asarray(nums, float)), strs


class NumberGroundingValidator:
    def __init__(self, payload: Any) -> None:
        self.values, self.strings = flatten(payload)

    def _has(self, v: float, tol: float) -> bool:
        lo = np.searchsorted(self.values, v - tol - 1e-9 * abs(v), "left")
        hi = np.searchsorted(self.values, v + tol + 1e-9 * abs(v), "right")
        return bool(hi > lo)

    def check(self, text: str) -> list[Violation]:
        text = LIST_MARK.sub("", text)
        out: list[Violation] = []
        for d in DATE.findall(text):
            if d not in self.strings:
                out.append(Violation(d, "date not in payload"))
        text = DATE.sub(" ", text)
        for m in NUM.finditer(text):
            start = m.start()
            prev = text[start - 1] if start else " "
            if prev.isalnum() or prev in "_-/.":
                continue  # part of an identifier / version / ratio label
            raw, unit = m.group("num"), (m.group("unit") or "")
            nxt = text[m.end() : m.end() + 1]
            if not unit and (nxt.isalpha() or nxt in "_"):
                continue  # e.g. "P50", "3rd", "13wk" → identifiers / ordinals
            if not self._grounded(raw, unit):
                out.append(Violation(m.group(0).strip(), "number not in payload"))
        return out

    def _grounded(self, raw: str, unit: str) -> bool:
        s = raw.replace(",", "")
        decimals = len(s.split(".")[1]) if "." in s else 0
        v = float(s)
        half = 0.5 * 10.0**-decimals
        if unit == "%":
            return self._has(v / 100, half / 100) or self._has(v, half)
        mult = UNITS.get(unit.lower(), 1.0) if unit else 1.0
        assert mult is not None
        if self._has(v * mult, half * mult):
            return True
        # unitless integers written with thousands separators may be rounded kg/₹ values
        return not unit and decimals == 0 and v >= 1000 and self._has(v, 0.5)

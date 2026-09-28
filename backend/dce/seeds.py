"""Deterministic seed derivation (ARCH §8): sub-seeds from (seed, module, series_id, ...)."""

from __future__ import annotations

import hashlib

import numpy as np


def derive_seed(seed: int, *parts: object) -> int:
    """Stable 63-bit sub-seed; independent of Python's hash randomization and call order."""
    key = "|".join([str(seed), *(str(p) for p in parts)]).encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") >> 1


def rng(seed: int, *parts: object) -> np.random.Generator:
    return np.random.default_rng(derive_seed(seed, *parts))

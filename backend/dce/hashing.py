"""Content hashing for datasets and configs (ARCH §5.1, §5.12; IDEATION P5)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def dataset_hash(world_dir: Path, filenames: Iterable[str]) -> str:
    """SHA-256 over sorted `name:sha256` lines of the given files that exist in `world_dir`.

    Only contract files are passed in, so READMEs or markers never change the hash.
    """
    h = hashlib.sha256()
    for name in sorted(filenames):
        path = world_dir / name
        if path.is_file():
            h.update(f"{name}:{file_sha256(path)}\n".encode())
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def config_hash(config: Mapping[str, Any]) -> str:
    """Hash of the effective config, independent of key order and YAML formatting."""
    return hashlib.sha256(canonical_json(config).encode()).hexdigest()

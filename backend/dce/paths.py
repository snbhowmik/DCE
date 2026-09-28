"""Repository-root-relative paths, independent of the current working directory."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Return the repo root: $DCE_ROOT if set, else the first ancestor holding config/ and docs/."""
    env = os.environ.get("DCE_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "config").is_dir() and (parent / "docs").is_dir():
            return parent
    raise RuntimeError("cannot locate repo root; set DCE_ROOT")


ROOT = repo_root()
CONFIG_DIR = ROOT / "config"
CONTRACT_DIR = ROOT / "contract"
DATA_DIR = ROOT / "data"
INCOMING_DIR = DATA_DIR / "incoming"
FIXTURES_DIR = DATA_DIR / "fixtures"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = ROOT / "reports"

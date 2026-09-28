"""Code version capture for run provenance (IDEATION P5)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dce import paths


def git_sha(repo: Path | None = None) -> str:
    """HEAD SHA, suffixed `-dirty` if tracked files changed; `unknown` outside git."""
    cwd = repo or paths.ROOT
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{sha}-dirty" if dirty else sha

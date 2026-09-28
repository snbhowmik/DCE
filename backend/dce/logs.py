"""structlog JSON logging with run_id bound via contextvars (ARCH §8)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

_configured = False


def configure_logging(level: int = logging.INFO, json: bool = True) -> None:
    global _configured
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    if not _configured:
        configure_logging()
    return structlog.get_logger(name) if name else structlog.get_logger()


@contextmanager
def bound(**kwargs: Any) -> Iterator[None]:
    """Bind context (e.g. run_id) to every log line inside the block."""
    with structlog.contextvars.bound_contextvars(**kwargs):
        yield

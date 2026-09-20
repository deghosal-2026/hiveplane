"""Structured JSON logging for CauterRule.

Provides :class:`JSONFormatter` and helpers ``setup_logging`` / ``get_logger``.
Output is one JSON object per line with ``timestamp``, ``level``, ``logger``,
``message`` plus any ``extra`` fields supplied at the call site.

Example::

    from cauterule.log import get_logger, setup_logging

    setup_logging(level="INFO")
    log = get_logger(__name__)
    log.info("rule promoted", extra={"rule_id": "R-001"})
    # {"timestamp":"...","level":"INFO","logger":"...","message":"rule promoted","rule_id":"R-001"}
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

__all__ = ["JSONFormatter", "get_logger", "setup_logging"]


_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _parse_level(level: str | int) -> int:
    """Normalize *level* to a :mod:`logging` integer."""
    if isinstance(level, int):
        return level
    normalized = level.strip().upper()
    if normalized not in _LEVELS:
        raise ValueError(f"Unknown log level: {level!r}")  # noqa: TRY003
    return _LEVELS[normalized]


class JSONFormatter(logging.Formatter):
    """Format a :class:`logging.LogRecord` as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Return *record* as a JSON string."""
        # Base fields — always present.
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Preserve explicitly-passed ``extra`` keys while filtering stdlib
        # LogRecord attributes so we do not leak internals.
        standard = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
        }
        for key, value in record.__dict__.items():
            if key not in standard and not key.startswith("_"):
                # ``json.dumps`` must succeed — coerce non-serializable values
                # to their string representation rather than crashing.
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)

        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class _PlainFormatter(logging.Formatter):
    """Human-readable fallback when ``json_format`` is False."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")


def setup_logging(
    level: str | int = "INFO",
    *,
    json_format: bool = True,
    stream: TextIO | None = None,
) -> None:
    """Configure the root logger for structured output.

    Args:
        level: Log level as string (``DEBUG``/``INFO``/etc.) or int.
        json_format: When ``True`` use :class:`JSONFormatter`, else plain text.
        stream: Output stream. Defaults to ``sys.stderr``.
    """
    resolved = _parse_level(level)
    target: TextIO = stream if stream is not None else sys.stderr

    handler = logging.StreamHandler(target)
    handler.setLevel(resolved)
    handler.setFormatter(JSONFormatter() if json_format else _PlainFormatter())

    root = logging.getLogger()
    root.setLevel(resolved)
    # Avoid duplicate handlers on repeated calls (e.g. in tests).
    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger with *name*.

    Args:
        name: Logger name, typically ``__name__``.
    """
    return logging.getLogger(name)

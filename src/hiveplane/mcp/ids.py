"""Stable ULID-style identifiers for MCP tool onboarding (M44-03)."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(*, now: float | None = None) -> str:
    """Return a lexicographically sortable 26-character ULID."""
    milliseconds = int((now if now is not None else time.time()) * 1000)
    timestamp = ""
    for _ in range(10):
        timestamp = _ALPHABET[milliseconds & 31] + timestamp
        milliseconds >>= 5
    randomness = "".join(_ALPHABET[byte % 32] for byte in os.urandom(16))
    return timestamp + randomness


def new_tool_id() -> str:
    """Return a stable, opaque tool id (assigned once at onboarding)."""
    return f"tool-{new_ulid()}"

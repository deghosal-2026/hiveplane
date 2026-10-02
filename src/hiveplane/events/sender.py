"""HTTP delivery for fleet events (M56-05)."""

from __future__ import annotations

from typing import Any

import httpx


class HttpEventSender:
    """POSTs fleet events to subscriber webhooks (best effort, short timeout)."""

    def __init__(self, *, timeout: float = 5.0) -> None:
        self._timeout = timeout

    def __call__(self, url: str, payload: dict[str, Any]) -> None:
        response = httpx.post(url, json=payload, timeout=self._timeout)
        response.raise_for_status()

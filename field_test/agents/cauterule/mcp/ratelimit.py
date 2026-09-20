"""Per-client token-bucket rate limiting for MCP remote mode (#601)."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class RateLimitError(Exception):
    """Raised when a client exceeds its budget (maps to 429)."""

    status = 429

    def __init__(self, retry_after: float) -> None:
        """Store the retry-after hint."""
        msg = f"rate limit exceeded; retry after {retry_after:.1f}s"
        super().__init__(msg)
        self.retry_after = retry_after


class RateLimiter(ABC):
    """Rate-limiter interface (in-process default; Redis swappable)."""

    @abstractmethod
    def allow(self, client: str) -> float:
        """Consume one unit for *client*. Returns retry-after seconds (0 = allowed)."""
        ...


class TokenBucket(RateLimiter):
    """In-process token-bucket limiter with per-client buckets.

    Args:
        capacity: Maximum burst tokens per client.
        refill_per_min: Tokens refilled per minute.
    """

    def __init__(self, capacity: int = 60, refill_per_min: float = 30.0) -> None:
        """Set burst capacity and refill rate."""
        if capacity < 1:
            msg = "capacity must be >= 1"
            raise ValueError(msg)
        self.capacity = capacity
        self.refill_per_sec = refill_per_min / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, client: str) -> float:
        """Consume one unit. Returns 0 when allowed, else retry-after seconds."""
        now = time.monotonic()
        tokens, updated = self._buckets.get(client, (float(self.capacity), now))
        tokens = min(float(self.capacity), tokens + (now - updated) * self.refill_per_sec)
        if tokens >= 1.0:
            self._buckets[client] = (tokens - 1.0, now)
            return 0.0
        deficit = 1.0 - tokens
        retry_after = deficit / self.refill_per_sec if self.refill_per_sec > 0 else 60.0
        self._buckets[client] = (tokens, now)
        return retry_after

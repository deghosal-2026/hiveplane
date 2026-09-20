"""
In-memory rate limiter using a simple sliding-window counter.

Rate limits are configured per tool in config.yaml under a ``rate_limits``
section.  If a tool has no configured limit, calls are not rate-limited.
Per-agent limits are enforced when an agent identity is provided.
"""

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_MAX_REQUESTS = 120


class RateLimiter:
    """Track call counts per tool (and optionally per agent) within a sliding window."""

    def __init__(self, rate_limits: dict[str, dict] | None = None):
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._limits: dict[str, dict] = rate_limits or {}

    def check(self, tool_name: str, agent_id: str | None = None) -> tuple[bool, str]:
        """Check if *tool_name* (optionally per *agent_id*) is within its rate limit.

        Returns ``(allowed, error_message)``.  Always allows if no limit
        is configured for the tool.
        """
        limit_cfg = self._limits.get(tool_name)
        if not limit_cfg:
            return True, ""

        window = limit_cfg.get("window_seconds", DEFAULT_WINDOW_SECONDS)
        max_requests = limit_cfg.get("max_requests", DEFAULT_MAX_REQUESTS)
        per_agent = limit_cfg.get("per_agent", False)

        if per_agent and agent_id:
            bucket_key = f"{tool_name}:{agent_id}"
        else:
            bucket_key = tool_name

        now = time.monotonic()
        bucket = self._buckets[bucket_key]
        cutoff = now - window
        bucket[:] = [t for t in bucket if t > cutoff]

        if len(bucket) >= max_requests:
            wait = bucket[0] - cutoff
            return False, (
                f"Rate limit exceeded for {tool_name!r}: "
                f"{max_requests} requests per {window}s. "
                f"Retry in {max(1, int(wait))}s."
            )

        bucket.append(now)
        return True, ""

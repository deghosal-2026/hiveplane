"""Per-tenant API rate limiting with 429 + Retry-After (M56-04)."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from hiveplane.auth.models import AuthenticationError
from hiveplane.auth.service import AuthService

_EXEMPT_PREFIXES = ("/healthz", "/readyz", "/metrics", "/openapi.json", "/docs", "/redoc")


class RateLimitDecision(BaseModel):
    """The outcome of a rate-limit check for a tenant."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    remaining: int
    retry_after_seconds: int = 0


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, capacity: float, now: datetime) -> None:
        self.tokens = capacity
        self.updated = now


class TenantRateLimiter:
    """A process-local token bucket per tenant (M56-04)."""

    def __init__(
        self,
        *,
        requests_per_window: int,
        window_seconds: float,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._capacity = float(requests_per_window)
        self._rate = requests_per_window / window_seconds  # tokens per second
        self._buckets: dict[str, _Bucket] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    def check(self, tenant_id: str) -> RateLimitDecision:
        """Consume a request token for a tenant, refilling for elapsed time."""
        now = self._clock()
        bucket = self._buckets.get(tenant_id)
        if bucket is None:
            bucket = _Bucket(self._capacity, now)
            self._buckets[tenant_id] = bucket
        elapsed = max(0.0, (now - bucket.updated).total_seconds())
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return RateLimitDecision(allowed=True, remaining=int(bucket.tokens))
        deficit = 1.0 - bucket.tokens
        retry = max(1, math.ceil(deficit / self._rate))
        return RateLimitDecision(
            allowed=False, remaining=0, retry_after_seconds=retry
        )


def _tenant_for(request: Request) -> str:
    """Return the tenant whose bucket this request consumes.

    Keyed on the authenticated principal's tenant, never on a client-supplied
    header: a caller cannot mint fresh buckets by rotating ``X-Hiveplane-Tenant``
    or drain another tenant's bucket by naming it. With auth disabled every
    request shares the default bucket.
    """
    from hiveplane.config import get_settings

    settings = get_settings()
    if not settings.auth.enabled:
        return "default"
    service: AuthService | None = getattr(request.app.state, "auth_service", None)
    header = request.headers.get("Authorization", "")
    if service is None or not header.startswith("Bearer "):
        return "unauthenticated"
    try:
        return service.keys.authenticate(header[len("Bearer ") :]).tenant_id
    except AuthenticationError:
        return "unauthenticated"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rejects over-limit tenants with 429 and a Retry-After header."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        limiter: TenantRateLimiter | None = getattr(
            request.app.state, "rate_limiter", None
        )
        path = request.url.path
        if limiter is None or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)
        decision = limiter.check(_tenant_for(request))
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        return await call_next(request)

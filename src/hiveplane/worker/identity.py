"""Signed worker identity tokens (M46-05): issue, verify, revoke.

Tokens are HMAC-SHA256 signed, bound to ``worker_id``, tenant, and issue/expiry
times. Registration and lease acceptance verify the token; missing, forged,
expired, or revoked tokens are refused so a rogue host never executes fleet work.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hiveplane.worker.models import (
    WorkerIdentityError,
    WorkerToken,
    WorkerTokenIssued,
)

_DEFAULT_TTL_SECONDS = 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def new_token_id() -> str:
    """Return a fresh opaque token id."""
    return f"wt-{uuid4().hex[:20]}"


class WorkerIdentityService:
    """Issues and verifies signed worker tokens against a shared secret."""

    def __init__(
        self,
        secret: bytes,
        *,
        key_id: str = "worker-local",
        clock: Callable[[], datetime] | None = None,
        token_id_factory: Callable[[], str] = new_token_id,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        if not secret:
            raise WorkerIdentityError("worker identity secret must not be empty")
        self._secret = secret
        self._key_id = key_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_id_factory = token_id_factory
        self._ttl_seconds = ttl_seconds
        self._revoked: set[str] = set()

    def issue(
        self, worker_id: str, tenant_id: str, *, ttl_seconds: int | None = None
    ) -> WorkerTokenIssued:
        """Issue a signed token bound to a worker and tenant."""
        now = self._clock()
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        record = WorkerToken(
            token_id=self._token_id_factory(),
            worker_id=worker_id,
            tenant_id=tenant_id,
            key_id=self._key_id,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl),
        )
        payload = {
            "jti": record.token_id,
            "wid": worker_id,
            "tid": tenant_id,
            "kid": self._key_id,
            "iat": int(now.timestamp()),
            "exp": int(record.expires_at.timestamp()),
        }
        encoded = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return WorkerTokenIssued(token=f"{encoded}.{signature}", record=record)

    def verify(self, token: str, *, worker_id: str | None = None) -> WorkerToken:
        """Verify a token's signature, expiry, revocation, and worker binding."""
        if "." not in token:
            raise WorkerIdentityError("malformed worker token")
        encoded, signature = token.rsplit(".", 1)
        expected = _b64(
            hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise WorkerIdentityError("invalid worker token signature")
        try:
            payload = json.loads(_unb64(encoded))
        except (ValueError, json.JSONDecodeError) as exc:
            raise WorkerIdentityError("malformed worker token payload") from exc
        token_id = str(payload.get("jti", ""))
        if token_id in self._revoked:
            raise WorkerIdentityError("worker token revoked")
        now = self._clock()
        if int(payload.get("exp", 0)) <= int(now.timestamp()):
            raise WorkerIdentityError("worker token expired")
        bound_worker = str(payload.get("wid", ""))
        if worker_id is not None and bound_worker != worker_id:
            raise WorkerIdentityError("worker token does not match worker id")
        return WorkerToken(
            token_id=token_id,
            worker_id=bound_worker,
            tenant_id=str(payload.get("tid", "")),
            key_id=str(payload.get("kid", "")),
            issued_at=datetime.fromtimestamp(int(payload.get("iat", 0)), tz=UTC),
            expires_at=datetime.fromtimestamp(int(payload.get("exp", 0)), tz=UTC),
        )

    def revoke(self, token_id: str) -> None:
        """Add a token id to the revocation list (checked on verify)."""
        self._revoked.add(token_id)

    def revoked(self) -> list[str]:
        """Return the revoked token ids."""
        return sorted(self._revoked)

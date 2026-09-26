"""Webhook ingest: HMAC verification and replay protection (M27-02, D23).

The signature is HMAC-SHA256 over ``timestamp + "." + nonce + "." + raw_body``
using the trigger's shared secret; the raw bytes are signed, not a
re-serialization, so payload ordering is preserved. Requests outside the clock
skew window, with a bad/missing signature, or replaying a seen
``(trigger_id, nonce)`` pair are rejected with a status code. Every rejection is
audited when an audit log is configured.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from hiveplane.persistence.audit import AuditLog
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.triggers.store import TriggerStore


class IngestError(Exception):
    """Base class for webhook ingest rejections."""

    status_code = 400

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SignatureError(IngestError):
    """The HMAC signature is missing or does not match."""

    status_code = 401


class TimestampError(IngestError):
    """The request timestamp is outside the allowed skew window."""

    status_code = 401


class ReplayError(IngestError):
    """The ``(trigger_id, nonce)`` pair was seen inside the replay window."""

    status_code = 409


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    """A raw webhook delivery as received."""

    signature: str
    timestamp: str
    nonce: str
    body: bytes


def sign_webhook(secret: str, timestamp: int, nonce: str, body: bytes) -> str:
    """Return the hex HMAC-SHA256 signature for a webhook delivery."""
    message = f"{timestamp}.{nonce}.".encode() + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class WebhookVerifier:
    """Verifies HMAC signatures and rejects replays."""

    def __init__(
        self,
        *,
        store: TriggerStore,
        clock: Callable[[], datetime] | None = None,
        skew_seconds: int = 300,
        replay_window_seconds: int = 300,
        audit: AuditLog | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._skew_seconds = skew_seconds
        self._replay_window_seconds = replay_window_seconds
        self._audit = audit

    def verify(
        self,
        trigger_id: str,
        request: WebhookRequest,
        secret: str,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Verify a webhook delivery, raising an :class:`IngestError` on rejection."""
        now = self._clock()
        timestamp = self._parse_timestamp(trigger_id, request, now, ctx)
        self._check_signature(trigger_id, request, secret, timestamp, ctx)
        if not self._store.claim_nonce(
            trigger_id,
            request.nonce,
            seen_at=now,
            window_seconds=self._replay_window_seconds,
            ctx=ctx,
        ):
            self._reject(trigger_id, "replay", f"nonce {request.nonce!r} already seen", ctx)
            raise ReplayError(f"replayed nonce for trigger {trigger_id!r}")

    def _parse_timestamp(
        self,
        trigger_id: str,
        request: WebhookRequest,
        now: datetime,
        ctx: TenantContext,
    ) -> int:
        try:
            timestamp = int(request.timestamp)
        except (TypeError, ValueError) as exc:
            self._reject(trigger_id, "timestamp", "non-numeric timestamp", ctx)
            raise TimestampError("timestamp must be a unix integer") from exc
        if abs(now.timestamp() - timestamp) > self._skew_seconds:
            self._reject(
                trigger_id,
                "timestamp",
                f"timestamp {timestamp} outside {self._skew_seconds}s skew",
                ctx,
            )
            raise TimestampError(f"timestamp outside the {self._skew_seconds}s window")
        return timestamp

    def _check_signature(
        self,
        trigger_id: str,
        request: WebhookRequest,
        secret: str,
        timestamp: int,
        ctx: TenantContext,
    ) -> None:
        provided = request.signature.split("=", 1)[-1] if request.signature else ""
        expected = sign_webhook(secret, timestamp, request.nonce, request.body)
        if not provided or not hmac.compare_digest(expected, provided):
            self._reject(trigger_id, "bad_signature", "signature mismatch", ctx)
            raise SignatureError("webhook signature verification failed")

    def _reject(
        self,
        trigger_id: str,
        reason: str,
        detail: str,
        ctx: TenantContext,
    ) -> None:
        if self._audit is not None:
            self._audit.append(
                "trigger-ingest",
                "trigger.webhook.rejected",
                trigger_id,
                detail=f"{reason}: {detail}",
                ctx=ctx,
            )

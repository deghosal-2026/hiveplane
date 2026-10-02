"""Interactive and mobile approvals: signed, single-use, attributed (M51-03/04).

A Slack/Teams button or a mobile link carries a short-lived HMAC-signed token
bound to the approval id and run. Resolving verifies the signature and expiry,
binds the decision to exactly that approval, attributes the operator, and is
single-use — a replayed token or re-decision returns ``already_resolved``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hiveplane.delivery.models import (
    AlreadyResolvedError,
    ApprovalDecisionRecord,
    InvalidApprovalTokenError,
)
from hiveplane.delivery.store import DeliveryStore
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_token_id() -> str:
    """Return a fresh opaque approval-token id."""
    return f"apv-{uuid4().hex[:20]}"


@dataclass(frozen=True)
class InteractiveResolution:
    """A verified interactive approval decision ready to apply to its run."""

    approval_id: str
    run_id: str
    tenant_id: str
    operator_id: str
    decision: str
    reason: str | None
    channel: str
    ctx: TenantContext = DEFAULT_CONTEXT


class InteractiveApprovalService:
    """Issues and resolves signed approval tokens from chat and mobile."""

    def __init__(
        self,
        secret: bytes,
        store: DeliveryStore,
        *,
        resolve: Callable[[InteractiveResolution], None],
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: int = 900,
        token_id_factory: Callable[[], str] = new_token_id,
    ) -> None:
        if not secret:
            raise InvalidApprovalTokenError("approval secret must not be empty")
        self._secret = secret
        self._store = store
        self._resolve = resolve
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl_seconds
        self._token_id_factory = token_id_factory

    def issue(self, approval_id: str, run_id: str, *, tenant_id: str = "default") -> str:
        """Issue a short-lived signed approval token."""
        now = self._clock()
        payload = {
            "jti": self._token_id_factory(),
            "apv": approval_id,
            "run": run_id,
            "tid": tenant_id,
            "exp": int((now + timedelta(seconds=self._ttl)).timestamp()),
        }
        encoded = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def resolve(
        self,
        token: str,
        *,
        decision: str,
        operator_id: str,
        reason: str | None = None,
        channel: str = "api",
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ApprovalDecisionRecord:
        """Verify a token and resolve the bound approval exactly once."""
        if decision not in ("approve", "deny"):
            raise InvalidApprovalTokenError(f"invalid decision {decision!r}")
        payload, jti = self._verify(token)
        approval_id = str(payload["apv"])
        run_id = str(payload.get("run", ""))
        if str(payload.get("tid", "")) != tenant_id:
            raise InvalidApprovalTokenError("approval token tenant mismatch")
        if self._store.get_decision(approval_id, ctx=ctx) is not None:
            raise AlreadyResolvedError(approval_id)
        if not self._store.claim_token(jti):
            raise AlreadyResolvedError(approval_id)
        resolution = InteractiveResolution(
            approval_id=approval_id,
            run_id=run_id,
            tenant_id=tenant_id,
            operator_id=operator_id,
            decision=decision,
            reason=reason,
            channel=channel,
            ctx=ctx,
        )
        self._resolve(resolution)
        record = ApprovalDecisionRecord(
            approval_id=approval_id,
            operator_id=operator_id,
            decision=decision,
            channel=channel,
            reason=reason,
            tenant_id=tenant_id,
            decided_at=self._clock(),
        )
        self._store.save_decision(record, ctx=ctx)
        return record

    def decision(
        self, approval_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ApprovalDecisionRecord | None:
        """Return the recorded decision for an approval, if any."""
        return self._store.get_decision(approval_id, ctx=ctx)

    def _verify(self, token: str) -> tuple[dict[str, object], str]:
        if "." not in token:
            raise InvalidApprovalTokenError("malformed approval token")
        encoded, signature = token.rsplit(".", 1)
        expected = _b64(
            hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            raise InvalidApprovalTokenError("invalid approval token signature")
        try:
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        except (ValueError, json.JSONDecodeError) as exc:
            raise InvalidApprovalTokenError("malformed approval token payload") from exc
        if int(payload.get("exp", 0)) <= int(self._clock().timestamp()):
            raise InvalidApprovalTokenError("approval token expired")
        return payload, str(payload.get("jti", ""))

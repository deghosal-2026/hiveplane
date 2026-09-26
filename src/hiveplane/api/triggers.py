"""Trigger API: declarations, webhook ingest, events, runs, and DLQ (M27)."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from hiveplane.api.deps import (
    get_tenant_context,
    get_trigger_engine,
    get_trigger_secrets,
    get_trigger_store,
    get_webhook_verifier,
)
from hiveplane.fleet.triggers import (
    TriggerDlqEntry,
    TriggerEvent,
    TriggerOutcome,
    TriggerRun,
    TriggerSource,
)
from hiveplane.tenancy import TenantContext
from hiveplane.triggers.engine import TriggerDecision, TriggerEngine
from hiveplane.triggers.ingest import IngestError, WebhookRequest, WebhookVerifier
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import TriggerStore

router = APIRouter(prefix="/triggers", tags=["triggers"])

StoreDep = Annotated[TriggerStore, Depends(get_trigger_store)]
EngineDep = Annotated[TriggerEngine, Depends(get_trigger_engine)]
VerifierDep = Annotated[WebhookVerifier, Depends(get_webhook_verifier)]
SecretsDep = Annotated[dict[str, str], Depends(get_trigger_secrets)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]

_SIGNATURE = "X-HivePlane-Signature"
_TIMESTAMP = "X-HivePlane-Timestamp"
_NONCE = "X-HivePlane-Nonce"

#: HTTP status for a trigger decision, following D23's webhook contract.
_OUTCOME_STATUS = {
    TriggerOutcome.ACCEPTED: status.HTTP_202_ACCEPTED,
    TriggerOutcome.SUPPRESSED_COOLDOWN: status.HTTP_202_ACCEPTED,
    TriggerOutcome.DEDUPLICATED: status.HTTP_409_CONFLICT,
    TriggerOutcome.REJECTED_RATE: status.HTTP_429_TOO_MANY_REQUESTS,
    TriggerOutcome.REJECTED_BACKPRESSURE: status.HTTP_429_TOO_MANY_REQUESTS,
    TriggerOutcome.FAILED: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class WebhookResponse(TriggerDecision):
    """The webhook ingest response."""

    accepted: bool = False

    @classmethod
    def from_decision(cls, decision: TriggerDecision) -> WebhookResponse:
        """Build a response that reports whether a run was started."""
        return cls(**decision.model_dump(), accepted=decision.run_id is not None)


def _require(store: TriggerStore, trigger_id: str, ctx: TenantContext) -> TriggerSpec:
    spec = store.get_trigger(trigger_id, ctx=ctx)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"trigger {trigger_id!r} not found")
    return spec


@router.post("", response_model=TriggerSpec, status_code=status.HTTP_201_CREATED)
def create_trigger(spec: TriggerSpec, store: StoreDep, ctx: TenantDep) -> TriggerSpec:
    """Register or replace a trigger declaration."""
    store.save_trigger(spec, ctx=ctx)
    return spec


@router.get("", response_model=list[TriggerSpec])
def list_triggers(
    store: StoreDep, ctx: TenantDep, enabled: bool | None = None
) -> list[TriggerSpec]:
    """List trigger declarations, optionally filtered by enabled state."""
    return store.list_triggers(enabled=enabled, ctx=ctx)


@router.get("/dlq", response_model=list[TriggerDlqEntry])
def list_dlq(store: StoreDep, ctx: TenantDep) -> list[TriggerDlqEntry]:
    """List dead-lettered trigger deliveries awaiting replay."""
    return store.list_dlq(ctx=ctx)


@router.get("/{trigger_id}", response_model=TriggerSpec)
def get_trigger(trigger_id: str, store: StoreDep, ctx: TenantDep) -> TriggerSpec:
    """Return one trigger declaration."""
    return _require(store, trigger_id, ctx)


@router.delete("/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trigger(trigger_id: str, store: StoreDep, ctx: TenantDep) -> None:
    """Delete a trigger declaration and its history."""
    if not store.delete_trigger(trigger_id, ctx=ctx):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"trigger {trigger_id!r} not found")


@router.post("/{trigger_id}/enable", response_model=TriggerSpec)
def enable_trigger(trigger_id: str, store: StoreDep, ctx: TenantDep) -> TriggerSpec:
    """Enable a trigger."""
    return _set_enabled(store, trigger_id, True, ctx)


@router.post("/{trigger_id}/disable", response_model=TriggerSpec)
def disable_trigger(trigger_id: str, store: StoreDep, ctx: TenantDep) -> TriggerSpec:
    """Disable a trigger without deleting it."""
    return _set_enabled(store, trigger_id, False, ctx)


def _set_enabled(
    store: TriggerStore, trigger_id: str, enabled: bool, ctx: TenantContext
) -> TriggerSpec:
    spec = _require(store, trigger_id, ctx)
    updated = spec.model_copy(update={"enabled": enabled})
    store.save_trigger(updated, ctx=ctx)
    return updated


@router.get("/{trigger_id}/events", response_model=list[TriggerEvent])
def list_events(trigger_id: str, store: StoreDep, ctx: TenantDep) -> list[TriggerEvent]:
    """Return a trigger's evaluation history."""
    _require(store, trigger_id, ctx)
    return store.list_events(trigger_id, ctx=ctx)


@router.get("/{trigger_id}/runs", response_model=list[TriggerRun])
def list_runs(trigger_id: str, store: StoreDep, ctx: TenantDep) -> list[TriggerRun]:
    """Return a trigger's run linkage history."""
    _require(store, trigger_id, ctx)
    return store.list_runs(trigger_id, ctx=ctx)


@router.post(
    "/webhook/{trigger_id}",
    response_model=WebhookResponse,
    responses={401: {}, 409: {}, 429: {}},
)
async def ingest_webhook(
    trigger_id: str,
    request: Request,
    response: Response,
    store: StoreDep,
    engine: EngineDep,
    verifier: VerifierDep,
    secrets: SecretsDep,
    ctx: TenantDep,
) -> WebhookResponse:
    """Verify an HMAC webhook and evaluate it as a trigger event."""
    spec = _require(store, trigger_id, ctx)
    secret = secrets.get(trigger_id)
    if secret is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"no webhook secret configured for {trigger_id!r}"
        )
    body = await request.body()
    webhook = WebhookRequest(
        signature=request.headers.get(_SIGNATURE, ""),
        timestamp=request.headers.get(_TIMESTAMP, ""),
        nonce=request.headers.get(_NONCE, ""),
        body=body,
    )
    try:
        verifier.verify(trigger_id, webhook, secret, ctx=ctx)
    except IngestError as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc

    payload = json.loads(body) if body else {}
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "payload must be an object")
    decision = engine.ingest(spec, payload, source=TriggerSource.WEBHOOK, ctx=ctx)
    response.status_code = _OUTCOME_STATUS.get(decision.outcome, status.HTTP_202_ACCEPTED)
    return WebhookResponse.from_decision(decision)

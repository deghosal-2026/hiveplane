"""Rendered delivery envelopes with trace and attestation links (M51-02)."""

from __future__ import annotations

from collections.abc import Sequence

from hiveplane.delivery.models import DeliveryEnvelope, DeliveryEventType


def render_envelope(
    event_type: DeliveryEventType,
    *,
    tenant_id: str = "default",
    team_id: str | None = None,
    workload: str | None = None,
    run_id: str | None = None,
    approval_id: str | None = None,
    status: str | None = None,
    summary: str = "",
    base_url: str | None = None,
    public_verify_base_url: str | None = None,
    attestation_id: str | None = None,
    artifact_ids: Sequence[str] = (),
) -> DeliveryEnvelope:
    """Render a common envelope, linking the run trace, attestation, and public verify."""
    trace_link = f"{base_url.rstrip('/')}/runs/{run_id}" if base_url and run_id else None
    attestation_link = (
        f"{base_url.rstrip('/')}/attestations/{attestation_id}"
        if base_url and attestation_id
        else None
    )
    public_verify_link = (
        f"{public_verify_base_url.rstrip('/')}/verify/{attestation_id}"
        if public_verify_base_url and attestation_id
        else None
    )
    artifact_links = (
        [f"{base_url.rstrip('/')}/artifacts/{artifact_id}" for artifact_id in artifact_ids]
        if base_url
        else []
    )
    return DeliveryEnvelope(
        event_type=event_type,
        tenant_id=tenant_id,
        team_id=team_id,
        workload=workload,
        run_id=run_id,
        approval_id=approval_id,
        status=status,
        summary=summary,
        trace_link=trace_link,
        attestation_link=attestation_link,
        public_verify_link=public_verify_link,
        artifact_links=artifact_links,
    )

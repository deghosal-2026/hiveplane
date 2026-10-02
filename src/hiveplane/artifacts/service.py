"""Artifact capture, retrieval, and retention (M54-02/03, D21/D38)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from hiveplane.artifacts.backend import BlobBackend, content_address
from hiveplane.artifacts.models import (
    PurgeResult,
    RetentionPolicyNotFoundError,
)
from hiveplane.artifacts.store import ArtifactStore
from hiveplane.fleet.artifacts import Artifact, RetentionPolicy
from hiveplane.persistence.audit import AuditLog
from hiveplane.tenancy.context import DEFAULT_CONTEXT, SYSTEM_CONTEXT, TenantContext

if TYPE_CHECKING:
    from hiveplane.reporting.pii import PIIScrubber


class ArtifactNotFoundError(LookupError):
    """Raised when an artifact does not exist in the tenant."""

    def __init__(self, artifact_id: str) -> None:
        super().__init__(f"artifact not found: {artifact_id}")


def new_artifact_id() -> str:
    """Return a fresh opaque artifact id."""
    return f"art-{uuid4().hex[:20]}"


class ArtifactService:
    """Stores artifacts content-addressed and links them to runs."""

    def __init__(
        self,
        store: ArtifactStore,
        backend: BlobBackend,
        *,
        audit: AuditLog | None = None,
        scrubber: PIIScrubber | None = None,
        clock: Callable[[], datetime] | None = None,
        artifact_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._backend = backend
        self._audit = audit
        self._scrubber = scrubber
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = artifact_id_factory or new_artifact_id

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def bind_scrubber(self, scrubber: PIIScrubber) -> None:
        """Bind the PII scrubber after construction (wiring order)."""
        self._scrubber = scrubber

    def _scrub(self, data: bytes) -> bytes:
        """Redact PII from UTF-8 text payloads; leave binary bytes untouched."""
        if self._scrubber is None or b"\x00" in data:
            return data
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        return self._scrubber.scrub(text).text.encode("utf-8")

    def capture(
        self,
        *,
        tenant_id: str,
        run_id: str,
        filename: str,
        data: bytes,
        retention_policy_id: str | None = None,
        actor: str = "system",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Artifact:
        """Store ``data`` content-addressed and record its metadata."""
        data = self._scrub(data)
        digest = content_address(data)
        artifact_id = self._id_factory()
        key = f"{tenant_id}/{run_id}/{digest.split(':', 1)[1]}/{artifact_id}/{filename}"
        location = self._backend.put(key, data)
        policy = None
        if retention_policy_id is not None:
            policy = self._store.get_retention(
                retention_policy_id, tenant_id=tenant_id, ctx=ctx
            )
            if policy is None:
                raise RetentionPolicyNotFoundError(retention_policy_id)
        now = self._clock()
        expires_at = None
        if policy is not None:
            expires_at = now + timedelta(days=policy.retain_days)
        artifact = Artifact(
            artifact_id=artifact_id,
            tenant_id=tenant_id,
            run_id=run_id,
            location=location,
            size_bytes=len(data),
            content_hash=digest,
            retention_policy_id=retention_policy_id,
            created_at=now,
            expires_at=expires_at,
        )
        self._store.save_artifact(artifact, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                actor,
                "artifact.stored",
                artifact.artifact_id,
                detail=f"run={run_id} hash={digest}",
            )
        return artifact

    def get(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Artifact:
        """Return an artifact's metadata or raise if it is not in the tenant."""
        artifact = self._store.get_artifact(artifact_id, tenant_id=tenant_id, ctx=ctx)
        if artifact is None:
            raise ArtifactNotFoundError(artifact_id)
        return artifact

    def data(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> tuple[Artifact, bytes]:
        """Return an artifact's metadata and bytes."""
        artifact = self.get(artifact_id, tenant_id=tenant_id, ctx=ctx)
        return artifact, self._backend.get(artifact.location)

    def list(
        self,
        *,
        tenant_id: str,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Artifact]:
        """Return artifacts in the tenant, optionally for one run."""
        return self._store.list_artifacts(tenant_id=tenant_id, run_id=run_id, ctx=ctx)

    def delete(
        self,
        artifact_id: str,
        *,
        tenant_id: str,
        actor: str = "operator",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Delete an artifact's bytes and metadata, audited."""
        artifact = self.get(artifact_id, tenant_id=tenant_id, ctx=ctx)
        self._backend.delete(artifact.location)
        self._store.delete_artifact(artifact_id, tenant_id=tenant_id, ctx=ctx)
        if self._audit is not None:
            self._audit.append(actor, "artifact.deleted", artifact_id)

    def purge_tenant(self, tenant_id: str) -> int:
        """Delete every artifact blob and tenant retention policy for one tenant."""
        artifacts = self._store.list_artifacts(
            tenant_id=tenant_id, ctx=SYSTEM_CONTEXT
        )
        for artifact in artifacts:
            self._backend.delete(artifact.location)
        return self._store.purge_tenant(tenant_id)


class RetentionService:
    """Manages retention policies and purges expired artifacts."""

    def __init__(
        self,
        store: ArtifactStore,
        backend: BlobBackend,
        *,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._backend = backend
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def set_policy(
        self,
        policy: RetentionPolicy,
        *,
        actor: str = "operator",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> RetentionPolicy:
        """Create or update a retention policy."""
        self._store.save_retention(policy, ctx=ctx)
        if self._audit is not None:
            self._audit.append(
                actor,
                "retention.policy.set",
                policy.policy_id,
                detail=f"retain_days={policy.retain_days} hold={policy.legal_hold}",
            )
        return policy

    def policies(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RetentionPolicy]:
        """Return a tenant's retention policies."""
        return self._store.list_retention(tenant_id=tenant_id, ctx=ctx)

    def policy_tenants(self) -> list[str]:
        """Return every tenant with a persisted retention policy (sweep discovery)."""
        return self._store.retention_tenants()

    def run_has_artifacts(
        self,
        *,
        tenant_id: str,
        run_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> bool:
        """Return True when a run still owns artifact metadata (blobs follow them)."""
        return bool(
            self._store.list_artifacts(tenant_id=tenant_id, run_id=run_id, ctx=ctx)
        )

    def purge_due(
        self,
        *,
        tenant_id: str | None = None,
        at: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PurgeResult:
        """Delete expired artifacts, skipping any under legal hold."""
        now = at or self._clock()
        purged: list[str] = []
        skipped: list[str] = []
        for artifact in self._store.expired(now=now, tenant_id=tenant_id, ctx=ctx):
            policy = (
                self._store.get_retention(
                    artifact.retention_policy_id, tenant_id=artifact.tenant_id, ctx=ctx
                )
                if artifact.retention_policy_id is not None
                else None
            )
            if policy is not None and policy.legal_hold:
                skipped.append(artifact.artifact_id)
                continue
            self._backend.delete(artifact.location)
            self._store.delete_artifact(
                artifact.artifact_id, tenant_id=artifact.tenant_id, ctx=ctx
            )
            purged.append(artifact.artifact_id)
            if self._audit is not None:
                self._audit.append(
                    "system", "artifact.purged", artifact.artifact_id
                )
        return PurgeResult(
            purged=purged, skipped_legal_hold=skipped, completed_at=now
        )

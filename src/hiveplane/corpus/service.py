"""Publishing, immutable versioning, and sharing of corpora (M55-03)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hiveplane.certification.binding import canonical_json
from hiveplane.certification.models import BenchmarkCorpus
from hiveplane.corpus.models import CorpusRelease
from hiveplane.corpus.store import CorpusReleaseStore
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext


class CorpusImmutabilityError(ValueError):
    """Raised when republishing a version with different content."""

    def __init__(self, corpus_id: str, version: int) -> None:
        super().__init__(
            f"corpus {corpus_id!r} version {version} already exists with different "
            "content; versions are immutable"
        )


def corpus_content_hash(corpus: BenchmarkCorpus) -> str:
    """Return the stable content hash of a corpus."""
    payload = canonical_json(corpus.model_dump(mode="json", by_alias=True))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


class CorpusService:
    """Publishes immutable corpus versions and lists/exports them."""

    def __init__(
        self,
        store: CorpusReleaseStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def publish(
        self,
        corpus: BenchmarkCorpus,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CorpusRelease:
        """Publish a corpus version; identical republish is idempotent."""
        content_hash = corpus_content_hash(corpus)
        existing = self._store.get(corpus.id, corpus.version, tenant_id=tenant_id, ctx=ctx)
        if existing is not None:
            if existing.content_hash != content_hash:
                raise CorpusImmutabilityError(corpus.id, corpus.version)
            return existing
        release = CorpusRelease(
            corpus_id=corpus.id,
            version=corpus.version,
            content_hash=content_hash,
            corpus=corpus.model_dump(mode="json", by_alias=True),
            created_at=self._clock(),
            tenant_id=tenant_id,
        )
        self._store.add(release, ctx=ctx)
        return release

    def get(
        self,
        corpus_id: str,
        version: int,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CorpusRelease:
        """Return a published corpus release or raise."""
        release = self._store.get(corpus_id, version, tenant_id=tenant_id, ctx=ctx)
        if release is None:
            raise CorpusImmutabilityError(corpus_id, version)
        return release

    def releases(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CorpusRelease]:
        """Return a tenant's published corpus releases."""
        return self._store.list(tenant_id=tenant_id, ctx=ctx)

    def export_document(
        self,
        corpus_id: str,
        version: int,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> dict[str, Any]:
        """Return the corpus document for sharing (e.g. via a fleet bundle)."""
        return self._store.get(
            corpus_id, version, tenant_id=tenant_id, ctx=ctx
        ).corpus  # type: ignore[union-attr]

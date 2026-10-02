"""Corpus versioning integration for the learning loop (M36-04).

Approved candidates are staged for the **next** corpus version: integrating a
base corpus appends the approved candidates' tasks and bumps the version. The
cut is recorded immutably and the operation is idempotent — integrating the same
inputs twice yields the same version and task set.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import BenchmarkCorpus
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.corpus_store import CorpusVersionStore
from hiveplane.learning.models import CandidateStatus, CorpusVersion
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class CorpusVersionService:
    """Stages approved candidates into the next corpus version."""

    def __init__(
        self,
        store: CorpusVersionStore,
        *,
        candidates: CandidateService,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._candidates = candidates
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"cv-{uuid.uuid4().hex[:12]}")

    def integrate(
        self,
        workload: str,
        base: BenchmarkCorpus,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> BenchmarkCorpus:
        """Return the corpus the next certification should run.

        Appends every approved candidate's task and bumps the version; with no
        approved candidates the base corpus is returned unchanged.
        """
        approved = self._candidates.list(
            workload=workload, status=CandidateStatus.APPROVED, ctx=ctx
        )
        if not approved:
            return base
        tasks = list(base.tasks) + [candidate.to_task() for candidate in approved]
        version = base.version + 1
        if self._store.get_version(base.id, version, ctx=ctx) is None:
            self._store.add_version(
                CorpusVersion(
                    corpus_version_id=self._id_factory(),
                    corpus_id=base.id,
                    version=version,
                    task_ids=[task.id for task in tasks],
                    created_at=self._clock(),
                    tenant_id=ctx.tenant_id,
                ),
                ctx=ctx,
            )
        return base.model_copy(update={"version": version, "tasks": tasks})

    def versions(
        self, corpus_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CorpusVersion]:
        """Return the cut versions of a corpus, oldest first."""
        return self._store.list_versions(corpus_id, ctx=ctx)

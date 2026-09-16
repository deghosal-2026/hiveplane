"""Certification workflow: run a corpus, certify, and record (M7, #25).

The coordinator is the orchestration seam behind the certification API and CLI:
it resolves a workload's corpus, runs the benchmark through the configured
executor, evaluates and signs the result, and stores the record for inspection
and comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hiveplane.certification.corpus import load_corpus
from hiveplane.certification.diff import regression_diff
from hiveplane.certification.errors import CertificationNotFoundError, CorpusError
from hiveplane.certification.models import (
    CertificationRecord,
    CertificationStatus,
    Environment,
    RegressionDiff,
    TargetContext,
)
from hiveplane.certification.runner import BENCHMARK_VERSION, BenchmarkRunner, TaskExecutor
from hiveplane.certification.service import CertificationService
from hiveplane.certification.store import CertificationStore
from hiveplane.core.spec import canonical_model_identity
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService


class CertificationCoordinator:
    """Runs benchmarks, certifies workloads, and records the outcomes."""

    def __init__(
        self,
        registry: RegistryService,
        service: CertificationService,
        store: CertificationStore,
        *,
        executor: TaskExecutor,
        corpora_dir: str | Path,
        environment: Environment,
        benchmark_version: str = BENCHMARK_VERSION,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._service = service
        self._store = store
        self._executor = executor
        self._corpora_dir = Path(corpora_dir)
        self._environment = environment
        self._benchmark_version = benchmark_version
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def store(self) -> CertificationStore:
        """Return the record store backing this coordinator."""
        return self._store

    def certify(
        self,
        workload: str,
        *,
        target_context: TargetContext,
        corpus_ref: str | None = None,
    ) -> CertificationRecord:
        """Run the workload's corpus, certify it, and store the record."""
        record = self._registry.get(workload)
        certification = record.manifest.spec.certification
        reference = corpus_ref or (
            certification.benchmark_corpus if certification is not None else None
        )
        if not reference:
            raise CorpusError(f"workload {workload!r} has no benchmark_corpus configured")
        corpus = load_corpus(self._resolve_corpus_path(reference))
        runner = BenchmarkRunner(
            self._executor,
            model_identity=_model_identity(record.manifest),
            benchmark_version=self._benchmark_version,
            environment=self._environment,
            clock=self._clock,
        )
        result = runner.run(
            corpus, workload_id=workload, manifest_version=record.current_version
        )
        previous = self._registry.list_attestations(workload)
        certification_record = self._service.certify_with_result(
            result,
            target_context=target_context,
            previous_attestation_id=(
                previous[-1].attestation_id if previous else None
            ),
        )
        self._store.add(certification_record)
        return certification_record

    def _resolve_corpus_path(self, reference: str) -> Path:
        """Resolve a corpus reference under the corpora root, rejecting escapes."""
        root = self._corpora_dir.resolve()
        candidate = (root / reference).resolve()
        if not candidate.is_relative_to(root):
            raise CorpusError(
                f"corpus reference {reference!r} escapes the corpora root {str(root)!r}"
            )
        return candidate

    def get(self, record_id: str) -> CertificationRecord:
        """Return a stored certification record or raise."""
        record = self._store.get(record_id)
        if record is None:
            raise CertificationNotFoundError(record_id)
        return record

    def list(
        self,
        *,
        workload: str | None = None,
        status: CertificationStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[CertificationRecord]:
        """List stored certification records, optionally filtered."""
        return self._store.list(
            workload=workload, status=status, limit=limit, offset=offset
        )

    def compare(self, before_id: str, after_id: str) -> RegressionDiff:
        """Return the task-level regression diff between two certifications."""
        before = self.get(before_id)
        after = self.get(after_id)
        return regression_diff(
            before.benchmark_result,
            after.benchmark_result,
            before_attestation_id=before.attestation.attestation_id,
            after_attestation_id=after.attestation.attestation_id,
        )


def _model_identity(manifest: AgentWorkload) -> str:
    identity = manifest.spec.model.identity
    if identity is None:
        return "unspecified"
    return canonical_model_identity(identity)

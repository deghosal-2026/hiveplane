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

from opentelemetry.util.types import AttributeValue

from hiveplane import metrics, telemetry
from hiveplane.certification.corpus import load_corpus
from hiveplane.certification.diff import build_regression_report, regression_diff
from hiveplane.certification.errors import CertificationNotFoundError, CorpusError
from hiveplane.certification.models import (
    BenchmarkCorpus,
    BenchmarkResult,
    BenchmarkTask,
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
from hiveplane.registry.models import WorkloadRecord
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
        executor_factory: Callable[[str, str | None], TaskExecutor] | None = None,
        benchmark_version: str = BENCHMARK_VERSION,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._service = service
        self._store = store
        self._executor = executor
        self._executor_factory = executor_factory
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
        model_identity: str | None = None,
    ) -> CertificationRecord:
        """Run the workload's corpus, certify it, and store the record."""
        record = self._registry.get(workload)
        attributes: dict[str, AttributeValue] = {
            "workload": workload,
            "target_context": target_context.value,
        }
        if record.team is not None:
            attributes["team"] = record.team
        with telemetry.span("certification", attributes=attributes) as active:
            certification_record = self._run_certification(
                record,
                workload,
                target_context=target_context,
                corpus_ref=corpus_ref,
                model_identity=model_identity,
            )
            active.set_attribute(
                "attestation_id", certification_record.attestation.attestation_id
            )
            active.set_attribute(
                "benchmark_run_id", certification_record.benchmark_result.benchmark_run_id
            )
            return certification_record

    def _run_certification(
        self,
        record: WorkloadRecord,
        workload: str,
        *,
        target_context: TargetContext,
        corpus_ref: str | None,
        model_identity: str | None = None,
    ) -> CertificationRecord:
        """Execute the benchmark and persist the resulting certification record."""
        result, corpus = self._execute(
            record, workload, corpus_ref=corpus_ref, model_identity=model_identity
        )
        previous = self._registry.list_attestations(workload)
        previous_record = (
            self._store.get(previous[-1].attestation_id) if previous else None
        )
        certification_record = self._service.certify_with_result(
            result,
            target_context=target_context,
            previous_attestation_id=(
                previous[-1].attestation_id if previous else None
            ),
        )
        if previous_record is not None:
            diff = regression_diff(
                previous_record.benchmark_result,
                result,
                before_attestation_id=previous_record.attestation.attestation_id,
                after_attestation_id=certification_record.attestation.attestation_id,
                task_catalog=_catalog(corpus),
                baseline_attestation_id=previous_record.attestation.attestation_id,
            )
            certification_record = certification_record.model_copy(
                update={
                    "regression_report": build_regression_report(
                        diff, generated_at=self._clock()
                    )
                }
            )
        self._store.add(certification_record)
        status = certification_record.certification.status
        metrics.get_metrics().record_certification(
            workload=workload,
            team=record.team,
            status=status.value,
            duration_seconds=(result.finished_at - result.started_at).total_seconds(),
        )
        if previous and status in (
            CertificationStatus.UNCERTIFIED,
            CertificationStatus.QUARANTINED,
        ):
            metrics.get_metrics().record_regression(workload=workload)
        return certification_record

    def benchmark(
        self,
        workload: str,
        *,
        target_context: TargetContext = TargetContext.STAGING,
        corpus_ref: str | None = None,
        model_identity: str | None = None,
    ) -> BenchmarkResult:
        """Run a workload's corpus without certifying (drift probing, M34)."""
        record = self._registry.get(workload)
        result, _ = self._execute(
            record, workload, corpus_ref=corpus_ref, model_identity=model_identity
        )
        return result

    def _execute(
        self,
        record: WorkloadRecord,
        workload: str,
        *,
        corpus_ref: str | None,
        model_identity: str | None = None,
    ) -> tuple[BenchmarkResult, BenchmarkCorpus]:
        """Run the workload's benchmark corpus and return the result and corpus."""
        certification = record.manifest.spec.certification
        reference = corpus_ref or (
            certification.benchmark_corpus if certification is not None else None
        )
        if not reference:
            raise CorpusError(f"workload {workload!r} has no benchmark_corpus configured")
        corpus = load_corpus(self._resolve_corpus_path(reference))
        pinned = _pinned_identity(record.manifest, model_identity)
        executor = (
            self._executor_factory(workload, pinned)
            if self._executor_factory is not None
            else self._executor
        )
        runner = BenchmarkRunner(
            executor,
            model_identity=pinned or "unspecified",
            benchmark_version=self._benchmark_version,
            environment=self._environment,
            clock=self._clock,
        )
        result = runner.run(
            corpus, workload_id=workload, manifest_version=record.current_version
        )
        return result, corpus

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
            task_catalog=self._corpus_catalog(after),
            baseline_attestation_id=before.attestation.attestation_id,
        )

    def compare_to_baseline(
        self, workload: str, after_id: str, *, baseline_id: str | None = None
    ) -> RegressionDiff:
        """Compare a certification to its baseline (M33-02).

        The baseline is an explicit attestation id when given, otherwise the
        most recent other ``certified`` record for the workload, falling back to
        the most recent other record. Comparison is deterministic.
        """
        after = self.get(after_id)
        if baseline_id is not None:
            baseline = self.get(baseline_id)
        else:
            records = [r for r in self.list(workload=workload) if r.record_id != after_id]
            certified = [
                r for r in records if r.certification.status is CertificationStatus.CERTIFIED
            ]
            pool = certified or records
            pool.sort(key=lambda r: (r.attestation.timestamp, r.record_id))
            if not pool:
                raise CertificationNotFoundError(f"no baseline for {workload!r}")
            baseline = pool[-1]
        return regression_diff(
            baseline.benchmark_result,
            after.benchmark_result,
            before_attestation_id=baseline.attestation.attestation_id,
            after_attestation_id=after.attestation.attestation_id,
            task_catalog=self._corpus_catalog(after),
            baseline_attestation_id=baseline.attestation.attestation_id,
        )

    def _corpus_catalog(self, record: CertificationRecord) -> dict[str, BenchmarkTask] | None:
        """Best-effort load of the corpus tasks behind a certification record."""
        workload = record.benchmark_result.workload_id
        try:
            manifest = self._registry.get(workload).manifest
        except Exception:
            return None
        certification = manifest.spec.certification
        reference = certification.benchmark_corpus if certification is not None else None
        if not reference:
            return None
        try:
            corpus = load_corpus(self._resolve_corpus_path(reference))
        except (CorpusError, OSError):
            return None
        return _catalog(corpus)


def _catalog(corpus: BenchmarkCorpus) -> dict[str, BenchmarkTask]:
    """Index a corpus by task id for replay-frame construction."""
    return {task.id: task for task in corpus.tasks}


def _pinned_identity(manifest: AgentWorkload, override: str | None) -> str | None:
    """Return the canonical pinned model identity, or None when unpinned.

    An explicit ``override`` (e.g. from the CLI) wins; otherwise the manifest's
    structured identity is canonicalized. ``None`` means the benchmark has no
    pinned model and the adapter executor will refuse to run (D19).
    """
    if override is not None:
        return override
    identity = manifest.spec.model.identity
    if identity is None:
        return None
    return canonical_model_identity(identity)

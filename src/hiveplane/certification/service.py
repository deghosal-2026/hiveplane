"""Certification service: evaluate, sign, and record attestations (M6, #23).

The service ties the engine and the registry together: it evaluates a benchmark
result, builds a signed attestation, stores it immutably, and advances the
workload's certification status by the matching lifecycle event.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hiveplane.certification.engine import CertificationEngine, workload_policy
from hiveplane.certification.models import (
    Attestation,
    BenchmarkResult,
    CertificationEvent,
    CertificationRecord,
    CertificationStatus,
    Environment,
    Signer,
    TargetContext,
)
from hiveplane.certification.runner import BENCHMARK_VERSION
from hiveplane.certification.signing import sign_attestation
from hiveplane.registry.service import RegistryService

#: Placeholder written before signing; ``canonical_payload`` excludes it.
UNSIGNED = "unsigned"


class CertificationService:
    """Evaluates benchmark results into signed, recorded certifications."""

    def __init__(
        self,
        engine: CertificationEngine,
        registry: RegistryService,
        *,
        private_key: Ed25519PrivateKey,
        environment: Environment,
        identity: str = "certification-service@hiveplane",
        key_id: str = "hp-signing-key-01",
        benchmark_version: str = BENCHMARK_VERSION,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._private_key = private_key
        self._environment = environment
        self._identity = identity
        self._key_id = key_id
        self._benchmark_version = benchmark_version
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"att-{uuid.uuid4().hex[:12]}")

    def certify(
        self,
        result: BenchmarkResult,
        *,
        target_context: TargetContext,
        previous_attestation_id: str | None = None,
    ) -> Attestation:
        """Evaluate, sign, store, and record an attestation for a benchmark result."""
        return self.certify_with_result(
            result,
            target_context=target_context,
            previous_attestation_id=previous_attestation_id,
        ).attestation

    def certify_with_result(
        self,
        result: BenchmarkResult,
        *,
        target_context: TargetContext,
        previous_attestation_id: str | None = None,
    ) -> CertificationRecord:
        """Like :meth:`certify` but returns the certification, attestation, and result.

        The production-survival count is read from the registry record, never from
        the caller, so a client cannot claim runs it did not survive.
        """
        record = self._registry.get(result.workload_id)
        spec = record.manifest.spec.certification
        policy = workload_policy(spec, self._engine.policy) if spec is not None else None
        certification = self._engine.evaluate(
            result,
            current_status=record.certification_status,
            target_context=target_context,
            production_runs_survived=record.production_runs_survived,
            policy=policy,
        )
        attestation = Attestation(
            attestation_id=self._id_factory(),
            workload_id=result.workload_id,
            manifest_version=result.manifest_version,
            benchmark_version=self._benchmark_version,
            benchmark_run_id=result.benchmark_run_id,
            corpus_id=result.corpus_id,
            corpus_version=result.corpus_version,
            model_identity=result.model_identity,
            status=certification.status,
            target_context=target_context,
            eval_summary=certification.eval_summary or result.to_eval_summary(),
            timestamp=certification.timestamp,
            environment=self._environment,
            signer=Signer(identity=self._identity, key_id=self._key_id, signature=UNSIGNED),
            previous_attestation_id=previous_attestation_id,
        )
        stored = self._registry.store_attestation(sign_attestation(attestation, self._private_key))
        if certification.status is not record.certification_status:
            event = _event_for(record.certification_status, certification.status)
            if event is not None:
                expires_at = stored.timestamp + timedelta(
                    seconds=(policy or self._engine.policy).re_cert_interval
                )
                self._registry.apply_attestation(stored, event=event, expires_at=expires_at)
        return CertificationRecord(
            record_id=stored.attestation_id,
            certification=certification,
            attestation=stored,
            benchmark_result=result,
        )


def _event_for(
    current: CertificationStatus, resolved: CertificationStatus
) -> CertificationEvent | None:
    """Map a resolved certification status onto the lifecycle event that caused it."""
    if current is CertificationStatus.QUARANTINED and resolved is CertificationStatus.PROVISIONAL:
        return CertificationEvent.RECOVER
    if resolved is CertificationStatus.PROVISIONAL:
        return CertificationEvent.STAGING_PASS
    if resolved is CertificationStatus.CERTIFIED:
        return CertificationEvent.PRODUCTION_PASS
    if resolved is CertificationStatus.QUARANTINED:
        return CertificationEvent.RECERT_FAIL
    return None

"""The defense facade invoked at the tool-call boundary (M39-01/02/03/05/06).

One object owns scanning, taint, egress, security-event recording, and
escalation so the boundary has a single, testable seam.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane.core.sandbox import SandboxSpec
from hiveplane.defense.egress import EgressDecision, EgressPolicy
from hiveplane.defense.escalation import AttemptEscalator
from hiveplane.defense.events import SecurityEvent, SecurityEventKind, SecurityEventStore
from hiveplane.defense.scanner import DefenseScanner, DetectorAction, DetectorConfig, ScanResult
from hiveplane.defense.taint import TaintDecision, TaintRegistry
from hiveplane.persistence.audit import AuditLog
from hiveplane.tenancy import DEFAULT_CONTEXT


class DefenseGuard:
    """Scans, taints, gates egress, records events, and escalates repeats."""

    def __init__(
        self,
        *,
        scanner: DefenseScanner | None = None,
        events: SecurityEventStore | None = None,
        taint: TaintRegistry | None = None,
        escalator: AttemptEscalator | None = None,
        audit: AuditLog | None = None,
        config: DetectorConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._scanner = scanner or DefenseScanner()
        self._events = events
        self._taint = taint or TaintRegistry()
        self._escalator = escalator
        self._audit = audit
        self._config = config or DetectorConfig()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"sec-{uuid4().hex}")

    def scan_output(
        self,
        *,
        run_id: str,
        workload: str,
        tool_id: str,
        text: str,
    ) -> ScanResult:
        """Scan tool output; record and escalate a deterministic block."""
        result = self._scanner.scan(text, config=self._config)
        if result.action is DetectorAction.BLOCK:
            first = result.matches[0] if result.matches else None
            if self._escalator is not None:
                self._escalator.record_injection(
                    run_id=run_id,
                    workload=workload,
                    detector_id=first.detector_id if first is not None else None,
                    detector_version=result.detector_set_version,
                    detail={
                        "tool_id": tool_id,
                        "detectors": [m.detector_id for m in result.matches],
                    },
                )
            else:
                self._record(
                    SecurityEventKind.INJECTION,
                    run_id=run_id,
                    workload=workload,
                    detector_id=first.detector_id if first is not None else None,
                    detector_version=result.detector_set_version,
                    detail={"tool_id": tool_id},
                )
        return result

    def bind_audit(self, audit: AuditLog | None) -> None:
        """Attach the audit log once it is available during app wiring."""
        self._audit = audit

    def mark_untrusted(self, *, run_id: str, source_id: str, kind: str = "tool") -> None:
        """Mark a value that entered the run as untrusted."""
        self._taint.for_run(run_id).mark(kind=kind, source_id=source_id)

    def gate_destructive(
        self,
        *,
        run_id: str,
        workload: str,
        tool_id: str,
        allow_untrusted: bool,
    ) -> TaintDecision:
        """Deny a destructive call while untrusted values are live."""
        decision = self._taint.for_run(run_id).gate_destructive(
            tool_id=tool_id, allow_untrusted=allow_untrusted
        )
        if decision.blocked:
            self._record(
                SecurityEventKind.TAINT_BLOCK,
                run_id=run_id,
                workload=workload,
                detail={
                    "tool_id": tool_id,
                    "sources": [source.source_id for source in decision.sources],
                },
            )
        return decision

    def check_egress(
        self,
        *,
        run_id: str,
        workload: str,
        tool_id: str,
        host: str,
        port: int | None,
        sandbox: SandboxSpec | None,
    ) -> EgressDecision:
        """Evaluate egress; record and audit a denial."""
        decision = EgressPolicy.from_sandbox(sandbox).check(host, port)
        if not decision.allowed:
            self._record(
                SecurityEventKind.EGRESS_DENIED,
                run_id=run_id,
                workload=workload,
                detail={
                    "tool_id": tool_id,
                    "host": host,
                    "port": port,
                    "denied_by": decision.rule,
                },
            )
            if self._audit is not None:
                self._audit.append(
                    "defense",
                    "egress.denied",
                    workload,
                    detail=f"{host}:{port} ({decision.rule})",
                )
        return decision

    def _record(
        self,
        kind: SecurityEventKind,
        *,
        run_id: str | None,
        workload: str | None,
        detector_id: str | None = None,
        detector_version: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> None:
        if self._events is None:
            return
        self._events.add(
            SecurityEvent(
                event_id=self._id_factory(),
                run_id=run_id,
                workload_id=workload,
                kind=kind,
                detector_id=detector_id,
                detector_version=detector_version,
                detail=detail or {},
                created_at=self._clock(),
                tenant_id=DEFAULT_CONTEXT.tenant_id,
            )
        )

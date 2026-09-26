"""Owner notification for quarantines via fan-out channels (M34-04, D15)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.drift.models import QuarantineRecord
from hiveplane.execution.fanout import DeliveryTransport


def _target(destination: FanOutDestination) -> str:
    return destination.url or destination.channel or destination.project or destination.type.value


def compose_quarantine_message(
    record: QuarantineRecord, *, owner: str | None = None
) -> dict[str, JsonValue]:
    """Build the ``certification.quarantined`` fan-out message (D15)."""
    assessment = record.assessment
    return {
        "type": "certification.quarantined",
        "workload_id": record.workload,
        "owner": owner,
        "quarantine_id": record.quarantine_id,
        "severity": record.severity.value,
        "reason": record.reason,
        "baseline_attestation_id": record.baseline_attestation_id,
        "action_required": "investigate drift, fix, re-certify, and reinstate",
        "evidence": record.evidence,
        "regression_diff": record.regression_diff,
        "pass_rate_before": assessment.pass_rate_before if assessment else None,
        "pass_rate_after": assessment.pass_rate_after if assessment else None,
        "consecutive_failures": assessment.consecutive_failures if assessment else None,
        "timestamp": record.timestamp.isoformat(),
    }


class DriftNotifier:
    """Delivers quarantine notices to the configured fan-out transports."""

    def __init__(
        self,
        transports: Mapping[FanOutType, DeliveryTransport],
        destinations: Sequence[FanOutDestination] = (),
        *,
        enabled: bool = True,
    ) -> None:
        self._transports = dict(transports)
        self._destinations = list(destinations)
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """Return whether notification is enabled."""
        return self._enabled

    def notify_quarantine(
        self, record: QuarantineRecord, *, owner: str | None = None
    ) -> list[str]:
        """Send the quarantine notice; return the targets that accepted it."""
        if not self._enabled:
            return []
        message = compose_quarantine_message(record, owner=owner)
        delivered: list[str] = []
        for destination in self._destinations:
            transport = self._transports.get(destination.type)
            if transport is None:
                continue
            target = _target(destination)
            try:
                transport.send(destination, message)
            except Exception:
                continue
            delivered.append(target)
        return delivered

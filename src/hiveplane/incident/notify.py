"""Owner broadcast for incident mode via fan-out channels (M53, D15, D36)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.execution.fanout import DeliveryTransport
from hiveplane.incident.models import IncidentRecord


def _target(destination: FanOutDestination) -> str:
    return (
        destination.url
        or destination.channel
        or destination.project
        or destination.type.value
    )


def compose_incident_message(record: IncidentRecord) -> dict[str, JsonValue]:
    """Build the ``fleet.halted`` fan-out message (D15)."""
    return {
        "type": "fleet.halted",
        "incident_id": record.incident_id,
        "scope": record.scope.value,
        "scope_ref": record.scope_ref,
        "trigger": record.trigger,
        "reason": record.reason,
        "actor": record.actor,
        "halted_at": record.halted_at.isoformat(),
        "action_required": (
            "investigate the incident; run `hiveplane fleet resume` to recover"
        ),
    }


class IncidentBroadcaster:
    """Delivers incident notices to the configured fan-out transports."""

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

    def notify(self, record: IncidentRecord) -> list[str]:
        """Send the incident notice; return the targets that accepted it."""
        if not self._enabled:
            return []
        message = compose_incident_message(record)
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

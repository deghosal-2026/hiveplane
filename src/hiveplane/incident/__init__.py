"""Incident mode: fleet-wide halt, drain, broadcast, and attributed recovery (M53)."""

from hiveplane.incident.models import HaltScope, IncidentRecord
from hiveplane.incident.notify import IncidentBroadcaster, compose_incident_message
from hiveplane.incident.service import (
    IncidentHaltGate,
    IncidentService,
    NoActiveIncidentError,
)
from hiveplane.incident.store import (
    IncidentStore,
    InMemoryIncidentStore,
    PostgresIncidentStore,
    build_incident_store,
)

__all__ = [
    "HaltScope",
    "InMemoryIncidentStore",
    "IncidentBroadcaster",
    "IncidentHaltGate",
    "IncidentRecord",
    "IncidentService",
    "IncidentStore",
    "NoActiveIncidentError",
    "PostgresIncidentStore",
    "build_incident_store",
    "compose_incident_message",
]

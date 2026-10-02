"""Federation of remote planes (M59-08, stretch)."""

from __future__ import annotations

from hiveplane.federation.models import (
    AggregateEntry,
    AggregateView,
    PlaneStatus,
    RemotePlane,
)
from hiveplane.federation.service import FederationService
from hiveplane.federation.store import (
    InMemoryRemotePlaneStore,
    RemotePlaneStore,
    build_remote_plane_store,
)

__all__ = [
    "AggregateEntry",
    "AggregateView",
    "FederationService",
    "InMemoryRemotePlaneStore",
    "PlaneStatus",
    "RemotePlane",
    "RemotePlaneStore",
    "build_remote_plane_store",
]

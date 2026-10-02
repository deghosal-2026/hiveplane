"""Federation: register remote planes and aggregate the fleet view (M59-08)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.federation.models import (
    AggregateEntry,
    AggregateView,
    PlaneStatus,
    RemotePlane,
)
from hiveplane.federation.store import RemotePlaneStore


class FederationService:
    """Registers remote planes and serves an aggregate fleet view."""

    def __init__(
        self,
        store: RemotePlaneStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._workload_counts: dict[str, int] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    def register(
        self, *, plane_id: str, name: str, base_url: str, workload_count: int = 0
    ) -> RemotePlane:
        """Register or update a remote plane (idempotent by ``plane_id``)."""
        existing = self._store.get(plane_id)
        plane = RemotePlane(
            plane_id=plane_id,
            name=name,
            base_url=base_url,
            registered_at=existing.registered_at if existing is not None else self._clock(),
            last_seen_at=self._clock(),
            status=PlaneStatus.HEALTHY,
        )
        self._store.save(plane)
        self._workload_counts[plane_id] = max(0, workload_count)
        return plane

    def list_planes(self) -> list[RemotePlane]:
        """Return every registered remote plane."""
        return self._store.list_planes()

    def aggregate(self) -> AggregateView:
        """Return the aggregate fleet view across registered planes."""
        entries = [
            AggregateEntry(
                plane_id=plane.plane_id,
                name=plane.name,
                base_url=plane.base_url,
                status=plane.status,
                workload_count=self._workload_counts.get(plane.plane_id, 0),
            )
            for plane in self._store.list_planes()
        ]
        return AggregateView(
            plane_count=len(entries),
            total_workloads=sum(entry.workload_count for entry in entries),
            entries=entries,
        )

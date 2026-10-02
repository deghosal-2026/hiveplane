"""Federation remote-plane storage (M59-08, stretch)."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.federation.models import RemotePlane


class RemotePlaneStore(Protocol):
    """Storage for registered remote planes (control-plane global)."""

    def save(self, plane: RemotePlane) -> None: ...

    def get(self, plane_id: str) -> RemotePlane | None: ...

    def list_planes(self) -> list[RemotePlane]: ...

    def clear(self) -> None: ...


class InMemoryRemotePlaneStore:
    """A process-local, thread-safe remote-plane store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._planes: dict[str, RemotePlane] = {}

    def save(self, plane: RemotePlane) -> None:
        """Insert or replace a remote plane keyed by id."""
        with self._lock:
            self._planes[plane.plane_id] = plane.model_copy(deep=True)

    def get(self, plane_id: str) -> RemotePlane | None:
        """Return a remote plane by id."""
        with self._lock:
            plane = self._planes.get(plane_id)
            return plane.model_copy(deep=True) if plane is not None else None

    def list_planes(self) -> list[RemotePlane]:
        """Return all planes ordered by id."""
        with self._lock:
            ordered = sorted(self._planes.values(), key=lambda plane: plane.plane_id)
            return [plane.model_copy(deep=True) for plane in ordered]

    def clear(self) -> None:
        """Delete all registered planes; used by tests."""
        with self._lock:
            self._planes.clear()


def build_remote_plane_store() -> RemotePlaneStore:
    """Build the configured remote-plane store (in-memory)."""
    return InMemoryRemotePlaneStore()

"""Registry storage abstraction and the in-memory implementation.

Part 9 (M18) adds the PostgreSQL-backed implementation of :class:`RegistryStore`.
Until then the registry runs on an in-memory store that satisfies the same
protocol, so the API and service layers are storage-agnostic.
"""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.certification.models import Attestation
from hiveplane.registry.models import (
    ToolRecord,
    TriggerRecord,
    WorkloadRecord,
    WorkloadVersion,
)


class RegistryStore(Protocol):
    """Storage interface for registry desired state and certification records."""

    def save_workload(self, record: WorkloadRecord) -> None: ...

    def get_workload(self, name: str) -> WorkloadRecord | None: ...

    def list_workloads(self) -> list[WorkloadRecord]: ...

    def delete_workload(self, name: str) -> bool: ...

    def add_version(self, version: WorkloadVersion) -> None: ...

    def list_versions(self, workload: str) -> list[WorkloadVersion]: ...

    def get_version(self, workload: str, version: int) -> WorkloadVersion | None: ...

    def save_version(self, version: WorkloadVersion) -> None: ...

    def add_attestation(self, attestation: Attestation) -> None: ...

    def get_attestation(self, attestation_id: str) -> Attestation | None: ...

    def list_attestations(self, workload: str) -> list[Attestation]: ...

    def save_tool(self, tool: ToolRecord) -> None: ...

    def get_tool(self, tool_id: str) -> ToolRecord | None: ...

    def list_tools(self) -> list[ToolRecord]: ...

    def add_trigger(self, trigger: TriggerRecord) -> None: ...

    def list_triggers(self, workload: str) -> list[TriggerRecord]: ...

    def delete_trigger(self, workload: str, trigger_id: str) -> bool: ...


class InMemoryRegistryStore:
    """A process-local, thread-safe registry store.

    Objects are copied on write and on read so callers cannot mutate stored
    state (attestations in particular must be immutable).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workloads: dict[str, WorkloadRecord] = {}
        self._versions: dict[str, dict[int, WorkloadVersion]] = {}
        self._attestations: dict[str, Attestation] = {}
        self._tools: dict[str, ToolRecord] = {}
        self._triggers: dict[str, dict[str, TriggerRecord]] = {}

    def save_workload(self, record: WorkloadRecord) -> None:
        with self._lock:
            self._workloads[record.name] = record.model_copy(deep=True)

    def get_workload(self, name: str) -> WorkloadRecord | None:
        with self._lock:
            record = self._workloads.get(name)
            return record.model_copy(deep=True) if record is not None else None

    def list_workloads(self) -> list[WorkloadRecord]:
        with self._lock:
            return [record.model_copy(deep=True) for record in self._workloads.values()]

    def delete_workload(self, name: str) -> bool:
        with self._lock:
            if name not in self._workloads:
                return False
            del self._workloads[name]
            self._versions.pop(name, None)
            self._triggers.pop(name, None)
            return True

    def add_version(self, version: WorkloadVersion) -> None:
        with self._lock:
            self._versions.setdefault(version.workload, {})[version.version] = (
                version.model_copy(deep=True)
            )

    def save_version(self, version: WorkloadVersion) -> None:
        self.add_version(version)

    def list_versions(self, workload: str) -> list[WorkloadVersion]:
        with self._lock:
            versions = self._versions.get(workload, {})
            ordered = sorted(versions.values(), key=lambda item: item.version)
            return [version.model_copy(deep=True) for version in ordered]

    def get_version(self, workload: str, version: int) -> WorkloadVersion | None:
        with self._lock:
            record = self._versions.get(workload, {}).get(version)
            return record.model_copy(deep=True) if record is not None else None

    def add_attestation(self, attestation: Attestation) -> None:
        with self._lock:
            self._attestations[attestation.attestation_id] = attestation.model_copy(deep=True)

    def get_attestation(self, attestation_id: str) -> Attestation | None:
        with self._lock:
            attestation = self._attestations.get(attestation_id)
            return attestation.model_copy(deep=True) if attestation is not None else None

    def list_attestations(self, workload: str) -> list[Attestation]:
        with self._lock:
            matching = [
                attestation
                for attestation in self._attestations.values()
                if attestation.workload_id == workload
            ]
            matching.sort(key=lambda item: item.timestamp)
            return [attestation.model_copy(deep=True) for attestation in matching]

    def save_tool(self, tool: ToolRecord) -> None:
        with self._lock:
            self._tools[tool.tool_id] = tool.model_copy(deep=True)

    def get_tool(self, tool_id: str) -> ToolRecord | None:
        with self._lock:
            tool = self._tools.get(tool_id)
            return tool.model_copy(deep=True) if tool is not None else None

    def list_tools(self) -> list[ToolRecord]:
        with self._lock:
            return [tool.model_copy(deep=True) for tool in self._tools.values()]

    def add_trigger(self, trigger: TriggerRecord) -> None:
        with self._lock:
            self._triggers.setdefault(trigger.workload, {})[trigger.trigger_id] = (
                trigger.model_copy(deep=True)
            )

    def list_triggers(self, workload: str) -> list[TriggerRecord]:
        with self._lock:
            triggers = self._triggers.get(workload, {}).values()
            ordered = sorted(triggers, key=lambda item: item.trigger_id)
            return [trigger.model_copy(deep=True) for trigger in ordered]

    def delete_trigger(self, workload: str, trigger_id: str) -> bool:
        with self._lock:
            triggers = self._triggers.get(workload)
            if not triggers or trigger_id not in triggers:
                return False
            del triggers[trigger_id]
            return True

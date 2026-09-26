"""The certified catalog the router may choose from (M30-01, M30-02).

Only workloads that pass admission for the target context are eligible
candidates; an uncertified workload is never offered to the classifier.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.run import AdmissionContext
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class CatalogCandidate(BaseModel):
    """A certified workload the router may consider."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    description: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class CertifiedCatalog(Protocol):
    """Supplies the certified workloads eligible for a target context."""

    def candidates(
        self, context: AdmissionContext, *, ctx: TenantContext = ...
    ) -> list[CatalogCandidate]: ...


class RegistryCatalog:
    """Builds candidates from the registry, admitting each workload."""

    def __init__(self, registry: RegistryService) -> None:
        self._registry = registry

    def candidates(
        self, context: AdmissionContext, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CatalogCandidate]:
        """Return admitted workloads for ``context`` (uncertified excluded)."""
        candidates: list[CatalogCandidate] = []
        for record in self._registry.list_workloads():
            if not self._registry.check_admission(record.name, context).admitted:
                continue
            metadata = record.manifest.metadata
            candidates.append(
                CatalogCandidate(
                    workload=record.name,
                    description=metadata.description or "",
                    labels=dict(metadata.labels),
                )
            )
        candidates.sort(key=lambda candidate: candidate.workload)
        return candidates


class StaticCatalog:
    """A fixed candidate set for tests and offline routing."""

    def __init__(self, candidates: list[CatalogCandidate] | list[str]) -> None:
        self._candidates = [
            CatalogCandidate(workload=candidate)
            if isinstance(candidate, str)
            else candidate
            for candidate in candidates
        ]

    def candidates(
        self, context: AdmissionContext, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CatalogCandidate]:
        """Return the configured candidates regardless of context."""
        return [candidate.model_copy() for candidate in self._candidates]

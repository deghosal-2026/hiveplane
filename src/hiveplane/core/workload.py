"""The AgentWorkload aggregate: metadata + spec + certification status."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.spec import WorkloadSpec
from hiveplane.core.types import Name


class Metadata(BaseModel):
    """Workload identity and ownership metadata."""

    model_config = ConfigDict(extra="forbid")

    name: Name
    owner: str = Field(min_length=1)
    team: str | None = None
    description: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class AgentWorkload(BaseModel):
    """A registered agent workload and its desired state."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: Literal["hiveplane/v1"] = Field(default="hiveplane/v1", alias="apiVersion")
    kind: Literal["AgentWorkload"] = "AgentWorkload"
    metadata: Metadata
    spec: WorkloadSpec

    @property
    def name(self) -> str:
        """The workload's DNS-safe name."""
        return self.metadata.name

    @property
    def owner(self) -> str:
        """The owning team."""
        return self.metadata.owner

    @property
    def team(self) -> str | None:
        """The team used for cost attribution and policy binding."""
        return self.metadata.team

    @property
    def certification_status(self) -> CertificationStatus:
        """The current certification status (uncertified when unspecified)."""
        if self.spec.certification is None:
            return CertificationStatus.UNCERTIFIED
        return self.spec.certification.status

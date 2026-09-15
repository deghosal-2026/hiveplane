"""Registry domain errors."""

from __future__ import annotations

from hiveplane.certification.models import CertificationStatus


class RegistryError(Exception):
    """Base class for registry errors."""


class WorkloadNotFoundError(RegistryError):
    """Raised when a workload does not exist in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"workload {name!r} not found")
        self.name = name


class WorkloadAlreadyExistsError(RegistryError):
    """Raised when registering a workload that already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"workload {name!r} already exists")
        self.name = name


class VersionNotFoundError(RegistryError):
    """Raised when a manifest version does not exist."""

    def __init__(self, name: str, version: int) -> None:
        super().__init__(f"workload {name!r} has no version {version}")
        self.name = name
        self.version = version


class AdmissionRefusedError(RegistryError):
    """Raised when a workload is refused admission to a target context."""

    def __init__(
        self,
        name: str,
        context: str,
        required: CertificationStatus,
        actual: CertificationStatus,
    ) -> None:
        super().__init__(
            f"workload {name!r} refused admission to {context}: "
            f"requires certification status {required.value!r}, has {actual.value!r}"
        )
        self.name = name
        self.context = context
        self.required = required
        self.actual = actual


class ReCertificationRequiredError(RegistryError):
    """Raised when promotion is blocked pending re-certification."""

    def __init__(self, name: str, changed_fields: list[str]) -> None:
        fields = ", ".join(changed_fields)
        super().__init__(
            f"workload {name!r} requires re-certification before promotion; "
            f"changed fields: {fields}"
        )
        self.name = name
        self.changed_fields = changed_fields


class UnknownToolError(RegistryError):
    """Raised when a manifest references a tool that is not registered."""

    def __init__(self, tool_id: str) -> None:
        super().__init__(f"tool {tool_id!r} is not registered")
        self.tool_id = tool_id


class ToolAlreadyExistsError(RegistryError):
    """Raised when registering a tool that already exists."""

    def __init__(self, tool_id: str) -> None:
        super().__init__(f"tool {tool_id!r} already exists")
        self.tool_id = tool_id


class DestructiveToolRequiresApprovalError(RegistryError):
    """Raised when a destructive tool is allowed without requiring approval."""

    def __init__(self, tool_id: str) -> None:
        super().__init__(
            f"destructive tool {tool_id!r} must set require_approval: true"
        )
        self.tool_id = tool_id


class AttestationNotFoundError(RegistryError):
    """Raised when an attestation does not exist."""

    def __init__(self, attestation_id: str) -> None:
        super().__init__(f"attestation {attestation_id!r} not found")
        self.attestation_id = attestation_id


class AttestationVerificationError(RegistryError):
    """Raised when an attestation signature cannot be verified."""

    def __init__(self, attestation_id: str) -> None:
        super().__init__(f"attestation {attestation_id!r} failed signature verification")
        self.attestation_id = attestation_id


class AttestationAlreadyExistsError(RegistryError):
    """Raised when writing an attestation id that already exists (append-only)."""

    def __init__(self, attestation_id: str) -> None:
        super().__init__(f"attestation {attestation_id!r} already exists and is immutable")
        self.attestation_id = attestation_id

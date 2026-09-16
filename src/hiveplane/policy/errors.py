"""Policy and approval domain errors."""

from __future__ import annotations


class PolicyError(Exception):
    """Base class for policy errors."""


class PolicyPackAlreadyExistsError(PolicyError):
    """Raised when saving a pack whose name is already registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"policy pack {name!r} already exists")
        self.name = name


class PolicyPackNotFoundError(PolicyError):
    """Raised when a policy pack is not found."""

    def __init__(self, name: str) -> None:
        super().__init__(f"policy pack {name!r} not found")
        self.name = name


class ApprovalNotFoundError(PolicyError):
    """Raised when an approval id is unknown."""

    def __init__(self, approval_id: str) -> None:
        super().__init__(f"approval {approval_id!r} not found")
        self.approval_id = approval_id


class ApprovalAlreadyDecidedError(PolicyError):
    """Raised when deciding an approval that is already resolved."""

    def __init__(self, approval_id: str) -> None:
        super().__init__(f"approval {approval_id!r} has already been decided")
        self.approval_id = approval_id

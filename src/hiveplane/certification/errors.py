"""Certification pipeline errors (M5-M7)."""

from __future__ import annotations


class CertificationError(Exception):
    """Base class for certification pipeline errors."""


class CorpusError(CertificationError):
    """Raised when a benchmark corpus cannot be parsed or fails validation."""


class UnsupportedCheckError(CertificationError):
    """Raised when a benchmark check type has no deterministic evaluator yet."""

    def __init__(self, check_type: object) -> None:
        self.check_type = check_type
        super().__init__(f"no evaluator implemented for check type {check_type!r}")


class CertificationNotFoundError(CertificationError):
    """Raised when a certification id is unknown to the record store."""

    def __init__(self, certification_id: str) -> None:
        self.certification_id = certification_id
        super().__init__(f"certification {certification_id!r} not found")

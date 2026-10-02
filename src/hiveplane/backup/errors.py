"""Backup/restore errors (M59-03)."""

from __future__ import annotations


class BackupError(Exception):
    """Base class for backup failures."""


class BackupIntegrityError(BackupError):
    """Raised when a backup fails integrity or signature verification."""


class BackupSchemaMismatchError(BackupError):
    """Raised when a backup was taken against an incompatible schema."""

    def __init__(self, expected: str, found: str) -> None:
        self.expected = expected
        self.found = found
        super().__init__(f"backup schema {found!r} does not match current head {expected!r}")

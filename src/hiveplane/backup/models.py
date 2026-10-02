"""Backup and restore models (M59-03)."""

from __future__ import annotations

from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent / "persistence" / "migrations"
)


def schema_head() -> str:
    """Return the current Alembic migration head (single source of truth)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:  # pragma: no cover - only if the migrations dir is empty
        raise RuntimeError("could not determine the Alembic migration head")
    return head


#: Highest Alembic revision this build understands; restore refuses a mismatch.
SCHEMA_HEAD = schema_head()


class BackupFile(BaseModel):
    """One named payload inside a backup archive."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    sha256: str = Field(min_length=64, max_length=64)
    records: int = Field(ge=0)


class BackupManifest(BaseModel):
    """The signed, self-describing index of a backup archive."""

    model_config = ConfigDict(extra="forbid")

    backup_id: str = Field(min_length=1, max_length=128)
    created_at: AwareDatetime
    hiveplane_version: str = Field(min_length=1, max_length=32)
    alembic_head: str = Field(min_length=1, max_length=32)
    algorithm: str = "sha256"
    files: list[BackupFile]
    total_records: int = Field(ge=0)


class BackupArchive(BaseModel):
    """A manifest plus its canonical JSON payloads and an Ed25519 signature."""

    model_config = ConfigDict(extra="forbid")

    manifest: BackupManifest
    payloads: dict[str, str]
    key_id: str = Field(min_length=1, max_length=64)
    signature: str = Field(min_length=1, max_length=512)


class RestoreCount(BaseModel):
    """The number of records restored for one target."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=64)
    restored: int = Field(ge=0)

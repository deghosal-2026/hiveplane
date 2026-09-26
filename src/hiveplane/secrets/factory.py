"""Build the secret service from settings (M45)."""

from __future__ import annotations

from hiveplane.config import Settings, get_settings
from hiveplane.secrets.crypto import LocalKeyProvider, load_or_create_master_key
from hiveplane.secrets.service import SecretService
from hiveplane.secrets.store import build_secret_store


def build_secret_service(settings: Settings | None = None) -> SecretService:
    """Build the configured secret service and local envelope key provider."""
    resolved = settings or get_settings()
    master_key = load_or_create_master_key(resolved.secrets.master_key_file)
    return SecretService(
        build_secret_store(resolved),
        LocalKeyProvider(master_key, key_id="local"),
    )

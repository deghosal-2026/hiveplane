"""Build the worker registry from settings (M46)."""

from __future__ import annotations

from hiveplane.config import Settings, get_settings
from hiveplane.secrets.crypto import load_or_create_master_key
from hiveplane.worker.identity import WorkerIdentityService
from hiveplane.worker.registry import WorkerRegistry
from hiveplane.worker.store import build_worker_store


def build_worker_registry(settings: Settings | None = None) -> WorkerRegistry:
    """Build the configured worker registry with a local identity key."""
    resolved = settings or get_settings()
    key = load_or_create_master_key(resolved.worker.identity_key_file)
    identity = WorkerIdentityService(key)
    return WorkerRegistry(
        build_worker_store(resolved),
        identity,
        heartbeat_timeout_s=resolved.worker.heartbeat_timeout_s,
        lease_ttl_s=resolved.worker.lease_ttl_s,
    )

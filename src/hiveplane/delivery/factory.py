"""Build the delivery/approval services from settings (M51)."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable

from hiveplane.config import Settings, get_settings
from hiveplane.delivery.approvals import InteractiveApprovalService, InteractiveResolution
from hiveplane.delivery.models import DeliveryChannel
from hiveplane.delivery.service import DeliveryService
from hiveplane.delivery.store import build_delivery_store
from hiveplane.secrets.crypto import load_or_create_master_key


class UrllibSender:
    """A best-effort HTTP sender for webhook-like channels."""

    def send(self, channel: DeliveryChannel, target: str, payload: dict[str, object]) -> None:
        """POST the payload to the target URL (no-op for non-URL channels)."""
        if not target.startswith(("http://", "https://")):
            return
        import json

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            target, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=10.0) as response:
            response.read()


def build_delivery_service(
    settings: Settings | None = None,
    *,
    resolve: Callable[[InteractiveResolution], None] | None = None,
) -> tuple[DeliveryService, InteractiveApprovalService]:
    """Build the delivery manager and interactive-approval service."""
    resolved = settings or get_settings()
    store = build_delivery_store(resolved)
    service = DeliveryService(store, UrllibSender())
    key = load_or_create_master_key(resolved.worker.identity_key_file + ".approvals")
    approvals = InteractiveApprovalService(
        key, store, resolve=resolve or (lambda _resolution: None)
    )
    return service, approvals

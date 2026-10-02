"""Build the cost service from settings (M49)."""

from __future__ import annotations

from hiveplane.config import Settings, get_settings
from hiveplane.cost.service import CostService
from hiveplane.cost.store import build_cost_store


def build_cost_service(settings: Settings | None = None) -> CostService:
    """Build the configured cost service."""
    resolved = settings or get_settings()
    return CostService(build_cost_store(resolved))

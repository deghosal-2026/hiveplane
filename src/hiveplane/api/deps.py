"""FastAPI dependencies for the registry API."""

from __future__ import annotations

from fastapi import Request

from hiveplane.execution.service import RunService
from hiveplane.registry.service import RegistryService


def get_registry_service(request: Request) -> RegistryService:
    """Return the registry service bound to the application state."""
    service: RegistryService = request.app.state.registry_service
    return service


def get_run_service(request: Request) -> RunService:
    """Return the run service bound to the application state."""
    service: RunService = request.app.state.run_service
    return service

"""FastAPI application factory for the HivePlane control plane.

M1 provides only the health surface; registry, execution, policy, and
intervention endpoints arrive in later milestones (M8-M12, M21-M22).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from hiveplane import __version__
from hiveplane.core.manifest import manifest_json_schema


def create_app() -> FastAPI:
    """Build and return the control-plane ASGI application."""
    app = FastAPI(
        title="HivePlane",
        version=__version__,
        summary="Control plane for production agent fleets.",
    )

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness probe: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    def readyz() -> dict[str, str]:
        """Readiness probe: the control plane can accept traffic."""
        return {"status": "ready"}

    @app.get("/manifest/schema", tags=["manifest"])
    def manifest_schema() -> dict[str, Any]:
        """Return the JSON Schema for the AgentWorkload manifest."""
        return manifest_json_schema()

    return app


app = create_app()

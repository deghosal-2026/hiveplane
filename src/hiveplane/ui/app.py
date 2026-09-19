"""Operator UI application: server-rendered screens over the control-plane API (M22, #83)."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from hiveplane import __version__
from hiveplane.config import get_settings
from hiveplane.ui.client import (
    ControlPlaneClient,
    ControlPlaneError,
    HttpControlPlaneClient,
)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def create_ui_app(client: ControlPlaneClient | None = None) -> FastAPI:
    """Build the operator UI application, injecting a control-plane client."""
    app = FastAPI(title="HivePlane Operator UI", version=__version__)
    app.state.control_plane = client or HttpControlPlaneClient(get_settings().ui.api_url)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.templates = templates

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness probe: the UI process is up."""
        return {"status": "ok"}

    @app.exception_handler(ControlPlaneError)
    async def _control_plane_error(
        request: Request, exc: ControlPlaneError
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": exc.status_code or 502, "detail": exc.detail},
            status_code=502,
        )

    return app


def main() -> None:
    """Console entrypoint for the operator UI server."""
    settings = get_settings()
    uvicorn.run(create_ui_app(), host=settings.ui.host, port=settings.ui.port)


app = create_ui_app()

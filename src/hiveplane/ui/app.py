"""Operator UI application: server-rendered screens over the control-plane API (M22, #83)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from hiveplane import __version__
from hiveplane.config import get_settings
from hiveplane.ui.client import (
    ControlPlaneClient,
    ControlPlaneError,
    HttpControlPlaneClient,
)
from hiveplane.ui.views import build_fleet, build_run_detail

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _redirect(run_id: str, **params: str) -> RedirectResponse:
    """Redirect back to a run detail page with a flash message."""
    query = urlencode(params)
    return RedirectResponse(f"/runs/{quote(run_id)}?{query}", status_code=303)


def _intervene(request: Request, action: str, run_id: str) -> RedirectResponse:
    """Run an intervention through the client, flashing success or failure."""
    client = request.app.state.control_plane
    try:
        getattr(client, action)(run_id)
    except ControlPlaneError as exc:
        return _redirect(run_id, error=exc.detail)
    return _redirect(run_id, ok=f"{action} requested")


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

    @app.get("/", response_class=HTMLResponse)
    def fleet(request: Request) -> HTMLResponse:
        """Fleet list: workload health at a glance."""
        control_plane = request.app.state.control_plane
        runs: list[dict[str, Any]] = control_plane.list_runs()
        view = build_fleet(control_plane.list_workloads(), runs, control_plane.get_spend())
        recent_runs = sorted(
            runs, key=lambda run: str(run.get("updated_at", "")), reverse=True
        )[:10]
        return templates.TemplateResponse(
            request, "fleet.html", {"view": view, "recent_runs": recent_runs}
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str) -> HTMLResponse:
        """Run detail: the full execution story."""
        try:
            story = request.app.state.control_plane.get_story(run_id)
        except ControlPlaneError as exc:
            if exc.status_code == 404:
                return templates.TemplateResponse(
                    request,
                    "not_found.html",
                    {"detail": exc.detail},
                    status_code=404,
                )
            raise
        view = build_run_detail(story)
        return templates.TemplateResponse(
            request,
            "run_detail.html",
            {
                "view": view,
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            },
        )

    @app.post("/runs/{run_id}/pause")
    def pause_run(request: Request, run_id: str) -> RedirectResponse:
        """Pause a run."""
        return _intervene(request, "pause", run_id)

    @app.post("/runs/{run_id}/resume")
    def resume_run(request: Request, run_id: str) -> RedirectResponse:
        """Resume a run."""
        return _intervene(request, "resume", run_id)

    @app.post("/runs/{run_id}/stop")
    def stop_run(request: Request, run_id: str) -> RedirectResponse:
        """Stop a run."""
        return _intervene(request, "stop", run_id)

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

"""Operator UI application: server-rendered screens over the control-plane API (M22, #83)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from hiveplane import __version__
from hiveplane.config import get_settings
from hiveplane.ui.client import (
    ControlPlaneClient,
    ControlPlaneError,
    HttpControlPlaneClient,
)
from hiveplane.ui.views import build_cert_dashboard, build_fleet, build_run_detail, build_spend

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


def _decide(
    request: Request,
    decision: str,
    approval_id: str,
    operator: str,
    reason: str | None,
) -> RedirectResponse:
    """Resolve an approval through the client, flashing success or failure."""
    client = request.app.state.control_plane
    try:
        getattr(client, decision)(approval_id, operator, reason)
    except ControlPlaneError as exc:
        return RedirectResponse(f"/approvals?error={quote(exc.detail)}", status_code=303)
    return RedirectResponse(
        f"/approvals?ok={quote(f'{decision}d {approval_id}')}", status_code=303
    )


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

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request) -> HTMLResponse:
        """Approval queue: resolve escalations raised by policy."""
        records: list[dict[str, Any]] = request.app.state.control_plane.list_approvals()
        pending = [record for record in records if record.get("status") == "pending"]
        resolved = [record for record in records if record.get("status") != "pending"]
        return templates.TemplateResponse(
            request,
            "approvals.html",
            {
                "pending": pending,
                "resolved": resolved,
                "ok": request.query_params.get("ok"),
                "error": request.query_params.get("error"),
            },
        )

    @app.post("/approvals/{approval_id}/approve")
    def approve_approval(
        request: Request,
        approval_id: str,
        operator: Annotated[str, Form(min_length=1)],
        reason: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        """Approve a request and resume the paused run."""
        return _decide(request, "approve", approval_id, operator, reason)

    @app.post("/approvals/{approval_id}/deny")
    def deny_approval(
        request: Request,
        approval_id: str,
        operator: Annotated[str, Form(min_length=1)],
        reason: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        """Deny a request and fail the paused run."""
        return _decide(request, "deny", approval_id, operator, reason)

    @app.get("/certifications", response_class=HTMLResponse)
    def certifications(request: Request) -> HTMLResponse:
        """Certification dashboard: status, trends, and quarantine history."""
        view = build_cert_dashboard(
            request.app.state.control_plane.list_certifications()
        )
        return templates.TemplateResponse(request, "certifications.html", {"view": view})

    @app.get("/spend", response_class=HTMLResponse)
    def spend(request: Request) -> HTMLResponse:
        """Spend view: attributed cost by workload and team."""
        view = build_spend(request.app.state.control_plane.get_spend())
        return templates.TemplateResponse(request, "spend.html", {"view": view})

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

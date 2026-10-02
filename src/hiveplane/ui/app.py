"""Operator UI application: server-rendered screens over the control-plane API (M22, #83)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlencode

import uvicorn
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from hiveplane import __version__
from hiveplane.auth.models import Permission
from hiveplane.config import DEFAULT_UI_SESSION_SECRET, get_settings
from hiveplane.tenancy.context import DEFAULT_TENANT_ID
from hiveplane.tenancy.models import Role
from hiveplane.ui.client import (
    ControlPlaneClient,
    ControlPlaneError,
    HttpControlPlaneClient,
)
from hiveplane.ui.deps import (
    IdentityDep,
    client_for,
    require_ui_permission,
)
from hiveplane.ui.rbac import UiIdentity, can, visible_actions
from hiveplane.ui.session import SESSION_COOKIE, UiSession, sign_session
from hiveplane.ui.stream import (
    STREAM_MAX_SECONDS,
    STREAM_POLL_SECONDS,
    _sequence,
    run_event_frames,
)
from hiveplane.ui.views import (
    DiffView,
    build_cert_dashboard,
    build_fleet,
    build_health_from_api,
    build_onboarding,
    build_queue,
    build_replay,
    build_roi,
    build_run_detail,
    build_search,
    build_spend,
    diff_from_regression,
    diff_from_replay,
    diff_from_runs,
    diff_from_version_diff,
)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled"})
_DECISION_PAST = {"approve": "approved", "deny": "denied"}


def _redirect(run_id: str, **params: str) -> RedirectResponse:
    """Redirect back to a run detail page with a flash message."""
    query = urlencode(params)
    return RedirectResponse(f"/runs/{quote(run_id)}?{query}", status_code=303)


def _page_context(extra: dict[str, Any], identity: UiIdentity) -> dict[str, Any]:
    """Combine a screen's data with the caller's identity and visible controls."""
    context: dict[str, Any] = {
        "identity": identity,
        "visible_actions": visible_actions(identity),
    }
    context.update(extra)
    return context


def _session_from_identity(
    who: dict[str, Any], api_key: str, tenant_id: str, ttl_seconds: int
) -> UiSession:
    """Build a session from a whoami payload, rejecting malformed responses."""
    raw_operator_id = who["operator_id"]
    raw_role = who["role"]
    if not raw_operator_id or not raw_role:
        raise ValueError("whoami response is missing operator_id or role")
    return UiSession(
        operator_id=str(raw_operator_id),
        tenant_id=str(who.get("tenant_id") or tenant_id or DEFAULT_TENANT_ID),
        role=Role(str(raw_role)),
        api_key=api_key,
        exp=int(time.time()) + ttl_seconds,
    )


def _intervene(request: Request, action: str, run_id: str) -> RedirectResponse:
    """Run an intervention through the client, flashing success or failure."""
    client = client_for(request)
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
    client = client_for(request)
    try:
        getattr(client, decision)(approval_id, operator, reason)
    except ControlPlaneError as exc:
        return RedirectResponse(f"/approvals?error={quote(exc.detail)}", status_code=303)
    return RedirectResponse(
        f"/approvals?ok={quote(f'{decision}d {approval_id}')}", status_code=303
    )


def _bulk_decide(
    request: Request, decision: str, approval_ids: list[str] | None, operator: str
) -> RedirectResponse:
    """Resolve many approvals through the client, flashing a per-id summary.

    Each id is attempted independently so one failure does not block the rest;
    failures are collected into the flash message with their id and detail.
    """
    if not approval_ids:
        return RedirectResponse(
            f"/approvals?error={quote('no approvals selected')}", status_code=303
        )
    client = client_for(request)
    failures: list[str] = []
    succeeded = 0
    for approval_id in approval_ids:
        try:
            getattr(client, decision)(approval_id, operator, None)
        except ControlPlaneError as exc:
            failures.append(f"{approval_id}: {exc.detail}")
        else:
            succeeded += 1
    verb = _DECISION_PAST[decision]
    if failures:
        summary = f"{verb} {succeeded}, failed {len(failures)}: {'; '.join(failures)}"
        return RedirectResponse(f"/approvals?error={quote(summary)}", status_code=303)
    return RedirectResponse(
        f"/approvals?ok={quote(f'{verb} {succeeded}')}", status_code=303
    )


async def _stream_run_events(
    client: ControlPlaneClient, run_id: str, *, seen: int
) -> AsyncIterator[str]:
    """Yield SSE frames for a run's new events until it terminates or times out.

    Polls the control plane off the event loop, advancing ``seen`` so each event
    is emitted once. Always closes with ``event: end`` and never outlives
    ``STREAM_MAX_SECONDS`` so an idle run cannot leak a poller.
    """
    deadline = time.monotonic() + STREAM_MAX_SECONDS
    while True:
        try:
            events = await asyncio.to_thread(client.get_run_events, run_id)
        except ControlPlaneError:
            yield "event: end\ndata: {}\n\n"
            return
        for frame in run_event_frames(events, seen=seen):
            yield frame
        sequences = [_sequence(event) for event in events]
        if sequences:
            seen = max(seen, max(sequences))
        try:
            run = await asyncio.to_thread(client.get_run, run_id)
        except ControlPlaneError:
            yield "event: end\ndata: {}\n\n"
            return
        if str(run.get("state")) in _TERMINAL_RUN_STATES or time.monotonic() >= deadline:
            yield "event: end\ndata: {}\n\n"
            return
        yield ": heartbeat\n\n"
        await asyncio.sleep(STREAM_POLL_SECONDS)


def _diff_view(request: Request, kind: str, params: Mapping[str, str]) -> DiffView:
    """Dispatch to the diff mapper for ``kind``, falling back to an empty view.

    Missing or malformed query parameters return an empty ``DiffView`` rather
    than raising, so the page stays renderable for any input.
    """
    client = client_for(request)
    try:
        if kind == "version":
            return diff_from_version_diff(
                client.get_version_diff(
                    params["name"], int(params["from_version"]), int(params["to_version"])
                )
            )
        if kind == "regression":
            return diff_from_regression(
                client.compare_certifications(params["before_id"], params["after_id"])
            )
        if kind == "runs":
            return diff_from_runs(
                client.get_story(params["before_id"]), client.get_story(params["after_id"])
            )
        if kind == "replay":
            return diff_from_replay(
                client.replay_diff(params["before_id"], params["after_id"])
            )
    except (KeyError, ValueError):
        return DiffView()
    return DiffView()


def _onboarding_done(client: ControlPlaneClient) -> set[str]:
    """Derive the completed onboarding step keys from live control-plane state."""
    done: set[str] = set()
    if client.list_adapters():
        done.add("connect")
    if client.list_workloads():
        done.add("register")
    if any(
        (record.get("certification") or {}).get("status") == "certified"
        for record in client.list_certifications()
    ):
        done.add("certify")
    if client.list_triggers():
        done.add("trigger")
    return done


def create_ui_app(client: ControlPlaneClient | None = None) -> FastAPI:
    """Build the operator UI application, injecting a control-plane client."""
    settings = get_settings()
    if settings.auth.enabled and settings.ui.session_secret == DEFAULT_UI_SESSION_SECRET:
        raise RuntimeError(
            "refusing to start with the default UI session secret while auth is "
            "enabled; set HIVEPLANE_UI__SESSION_SECRET to a private value"
        )
    app = FastAPI(title="HivePlane Operator UI", version=__version__)
    app.state.control_plane = client or HttpControlPlaneClient(settings.ui.api_url)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.templates = templates

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness probe: the UI process is up."""
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def fleet(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Fleet list: workload health at a glance."""
        control_plane = client_for(request)
        runs: list[dict[str, Any]] = control_plane.list_runs()
        view = build_fleet(control_plane.list_workloads(), runs, control_plane.get_spend())
        recent_runs = sorted(
            runs, key=lambda run: str(run.get("updated_at", "")), reverse=True
        )[:10]
        return templates.TemplateResponse(
            request,
            "fleet.html",
            _page_context(
                {
                    "view": view,
                    "recent_runs": recent_runs,
                    "fleet_state": control_plane.fleet_state(),
                    "ok": request.query_params.get("ok"),
                    "error": request.query_params.get("error"),
                },
                identity,
            ),
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(
        request: Request, run_id: str, identity: IdentityDep
    ) -> HTMLResponse:
        """Run detail: the full execution story."""
        control_plane = client_for(request)
        try:
            story = control_plane.get_story(run_id)
        except ControlPlaneError as exc:
            if exc.status_code == 404:
                return templates.TemplateResponse(
                    request,
                    "not_found.html",
                    _page_context({"detail": exc.detail}, identity),
                    status_code=404,
                )
            raise
        try:
            artifacts = control_plane.list_artifacts(run_id)
        except ControlPlaneError:
            artifacts = []
        view = build_run_detail(story, artifacts)
        return templates.TemplateResponse(
            request,
            "run_detail.html",
            _page_context(
                {
                    "view": view,
                    "ok": request.query_params.get("ok"),
                    "error": request.query_params.get("error"),
                },
                identity,
            ),
        )

    @app.get("/runs/{run_id}/stream")
    async def run_stream(
        request: Request, run_id: str, identity: IdentityDep, seen: int = 0
    ) -> StreamingResponse:
        """Stream a run's new events as Server-Sent Events until it ends."""
        return StreamingResponse(
            _stream_run_events(client_for(request), run_id, seen=seen),
            media_type="text/event-stream",
        )

    @app.post("/runs/{run_id}/pause")
    def pause_run(
        request: Request,
        run_id: str,
        _: Annotated[
            UiIdentity, Depends(require_ui_permission(Permission.RUN_INTERVENE))
        ],
    ) -> RedirectResponse:
        """Pause a run."""
        return _intervene(request, "pause", run_id)

    @app.post("/runs/{run_id}/resume")
    def resume_run(
        request: Request,
        run_id: str,
        _: Annotated[
            UiIdentity, Depends(require_ui_permission(Permission.RUN_INTERVENE))
        ],
    ) -> RedirectResponse:
        """Resume a run."""
        return _intervene(request, "resume", run_id)

    @app.post("/runs/{run_id}/stop")
    def stop_run(
        request: Request,
        run_id: str,
        _: Annotated[
            UiIdentity, Depends(require_ui_permission(Permission.RUN_INTERVENE))
        ],
    ) -> RedirectResponse:
        """Stop a run."""
        return _intervene(request, "stop", run_id)

    @app.post("/fleet/pause")
    def pause_fleet(
        request: Request,
        identity: IdentityDep,
        reason: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """Big red button: halt the fleet in incident mode."""
        if not can(identity, Permission.KILL_SWITCH):
            return RedirectResponse(
                f"/?error={quote('not permitted to halt the fleet')}", status_code=303
            )
        client = client_for(request)
        try:
            client.pause_fleet(identity.operator_id, reason or None)
        except ControlPlaneError as exc:
            return RedirectResponse(f"/?error={quote(exc.detail)}", status_code=303)
        return RedirectResponse(f"/?ok={quote('fleet halted')}", status_code=303)

    @app.post("/fleet/resume")
    def resume_fleet(request: Request, identity: IdentityDep) -> RedirectResponse:
        """Recover from incident mode with attribution."""
        if not can(identity, Permission.KILL_SWITCH):
            return RedirectResponse(
                f"/?error={quote('not permitted to resume the fleet')}", status_code=303
            )
        client = client_for(request)
        try:
            client.resume_fleet(identity.operator_id)
        except ControlPlaneError as exc:
            return RedirectResponse(f"/?error={quote(exc.detail)}", status_code=303)
        return RedirectResponse(f"/?ok={quote('fleet resumed')}", status_code=303)

    @app.post("/runs/{run_id}/feedback")
    def record_run_feedback(
        request: Request,
        run_id: str,
        identity: IdentityDep,
        verdict: Annotated[str, Form(min_length=1)],
        notes: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """Record operator feedback on a terminal run, attributed to the caller."""
        client = client_for(request)
        try:
            client.record_feedback(run_id, verdict, notes, identity.operator_id)
        except ControlPlaneError as exc:
            return _redirect(run_id, error=exc.detail)
        return _redirect(run_id, ok="feedback recorded")

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> HTMLResponse:
        """Render the login form; auth-disabled deployments may still log in."""
        identity = UiIdentity(role=Role.VIEWER)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "identity": identity,
                "visible_actions": visible_actions(identity),
                "error": request.query_params.get("error"),
            },
        )

    @app.post("/login")
    def login(
        request: Request,
        api_key: Annotated[str, Form(min_length=1)],
        tenant_id: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """Verify the API key with the control plane and start a signed session."""
        settings = get_settings()
        ttl_seconds = settings.ui.session_ttl_hours * 3600
        try:
            who = request.app.state.control_plane.with_token(api_key).whoami()
            session = _session_from_identity(who, api_key, tenant_id, ttl_seconds)
        except ControlPlaneError as exc:
            return RedirectResponse(f"/login?error={quote(exc.detail)}", status_code=303)
        except (KeyError, TypeError, ValueError):
            return RedirectResponse(
                f"/login?error={quote('invalid identity response')}", status_code=303
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            sign_session(session, settings.ui.session_secret),
            httponly=True,
            samesite="lax",
            secure=settings.ui.session_cookie_secure,
            max_age=ttl_seconds,
        )
        return response

    @app.post("/logout")
    def logout() -> RedirectResponse:
        """Clear the signed session cookie."""
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE, secure=get_settings().ui.session_cookie_secure
        )
        return response

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Approval queue: resolve escalations raised by policy."""
        records: list[dict[str, Any]] = client_for(request).list_approvals()
        pending = [record for record in records if record.get("status") == "pending"]
        resolved = [record for record in records if record.get("status") != "pending"]
        return templates.TemplateResponse(
            request,
            "approvals.html",
            _page_context(
                {
                    "pending": pending,
                    "resolved": resolved,
                    "ok": request.query_params.get("ok"),
                    "error": request.query_params.get("error"),
                },
                identity,
            ),
        )

    @app.post("/approvals/{approval_id}/approve")
    def approve_approval(
        request: Request,
        approval_id: str,
        identity: Annotated[UiIdentity, Depends(require_ui_permission(Permission.APPROVE))],
        reason: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        """Approve a request and resume the paused run, attributed to the caller."""
        return _decide(request, "approve", approval_id, identity.operator_id, reason)

    @app.post("/approvals/{approval_id}/deny")
    def deny_approval(
        request: Request,
        approval_id: str,
        identity: Annotated[UiIdentity, Depends(require_ui_permission(Permission.APPROVE))],
        reason: Annotated[str | None, Form()] = None,
    ) -> RedirectResponse:
        """Deny a request and fail the paused run, attributed to the caller."""
        return _decide(request, "deny", approval_id, identity.operator_id, reason)

    @app.post("/approvals/bulk")
    def bulk_approvals(
        request: Request,
        identity: Annotated[UiIdentity, Depends(require_ui_permission(Permission.APPROVE))],
        decision: Annotated[Literal["approve", "deny"], Form()],
        approval_id: Annotated[list[str] | None, Form()] = None,
    ) -> RedirectResponse:
        """Apply one decision to many approvals, flashing a per-id summary."""
        return _bulk_decide(request, decision, approval_id, identity.operator_id)

    @app.post("/approvals/{approval_id}/comment")
    def comment_approval(
        request: Request,
        approval_id: str,
        identity: Annotated[UiIdentity, Depends(require_ui_permission(Permission.APPROVE))],
        text: Annotated[str, Form(min_length=1)],
    ) -> RedirectResponse:
        """Append a comment to an approval request, attributed to the caller."""
        try:
            client_for(request).add_approval_comment(
                approval_id, identity.operator_id, text
            )
        except ControlPlaneError as exc:
            return RedirectResponse(f"/approvals?error={quote(exc.detail)}", status_code=303)
        return RedirectResponse(
            f"/approvals?ok={quote(f'comment added to {approval_id}')}", status_code=303
        )

    @app.post("/approvals/{approval_id}/delegate")
    def delegate_approval(
        request: Request,
        approval_id: str,
        identity: Annotated[UiIdentity, Depends(require_ui_permission(Permission.APPROVE))],
        assignee: Annotated[str, Form(min_length=1)],
    ) -> RedirectResponse:
        """Reassign an approval request, attributed to the delegating caller."""
        try:
            client_for(request).delegate_approval(
                approval_id, assignee, identity.operator_id
            )
        except ControlPlaneError as exc:
            return RedirectResponse(f"/approvals?error={quote(exc.detail)}", status_code=303)
        return RedirectResponse(
            f"/approvals?ok={quote(f'{approval_id} delegated to {assignee}')}",
            status_code=303,
        )

    @app.get("/certifications", response_class=HTMLResponse)
    def certifications(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Certification dashboard: status, trends, and quarantine history."""
        client = client_for(request)
        try:
            quarantines = client.list_quarantines()
        except ControlPlaneError:
            quarantines = []
        view = build_cert_dashboard(client.list_certifications(), quarantines)
        return templates.TemplateResponse(
            request,
            "certifications.html",
            _page_context({"view": view}, identity),
        )

    @app.get("/spend", response_class=HTMLResponse)
    def spend(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Spend view: attributed cost by workload and team."""
        view = build_spend(client_for(request).get_spend())
        return templates.TemplateResponse(
            request, "spend.html", _page_context({"view": view}, identity)
        )

    @app.get("/cost", response_class=HTMLResponse)
    def cost(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Cost view: spend by workload with an inline bar chart."""
        view = build_spend(client_for(request).get_spend())
        return templates.TemplateResponse(
            request, "cost.html", _page_context({"view": view}, identity)
        )

    @app.get("/roi", response_class=HTMLResponse)
    def roi(request: Request, identity: IdentityDep) -> HTMLResponse:
        """ROI view: spend vs value with flagged low-value rows."""
        view = build_roi(client_for(request).get_roi())
        return templates.TemplateResponse(
            request, "roi.html", _page_context({"view": view}, identity)
        )

    @app.get("/health", response_class=HTMLResponse)
    def health(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Health view: per-workload status, success rate, and error budget."""
        view = build_health_from_api(client_for(request).list_health())
        return templates.TemplateResponse(
            request, "health.html", _page_context({"view": view}, identity)
        )

    @app.get("/triggers", response_class=HTMLResponse)
    def triggers(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Trigger log: configured triggers with their source, target, and schedule."""
        records: list[dict[str, Any]] = client_for(request).list_triggers()
        return templates.TemplateResponse(
            request,
            "triggers.html",
            _page_context({"triggers": records}, identity),
        )

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Queue visualizer: depth, breakdowns, and waiting tasks."""
        view = build_queue(client_for(request).get_queue())
        return templates.TemplateResponse(
            request, "queue.html", _page_context({"view": view}, identity)
        )

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Global search across runs, approvals, and workloads."""
        query = request.query_params.get("q", "")
        if query:
            view = build_search(query, client_for(request).search(query))
        else:
            view = build_search("", [])
        return templates.TemplateResponse(
            request, "search.html", _page_context({"view": view}, identity)
        )

    @app.get("/onboarding", response_class=HTMLResponse)
    def onboarding(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Onboarding wizard: setup progress derived from live control-plane state."""
        view = build_onboarding(_onboarding_done(client_for(request)))
        return templates.TemplateResponse(
            request, "onboarding.html", _page_context({"view": view}, identity)
        )

    @app.get("/diff", response_class=HTMLResponse)
    def diff(request: Request, identity: IdentityDep) -> HTMLResponse:
        """Diff viewer: version, regression, or run-to-run comparisons."""
        params = request.query_params
        kind = params.get("kind", "version")
        view = _diff_view(request, kind, params)
        return templates.TemplateResponse(
            request,
            "diff.html",
            _page_context({"view": view, "kind": kind}, identity),
        )

    @app.get("/replay/{run_id}", response_class=HTMLResponse)
    def replay(request: Request, run_id: str, identity: IdentityDep) -> HTMLResponse:
        """Frame-by-frame replay of a run (side-effect free)."""
        view = build_replay(client_for(request).get_replay(run_id))
        return templates.TemplateResponse(
            request, "replay.html", _page_context({"view": view}, identity)
        )

    @app.exception_handler(ControlPlaneError)
    async def _control_plane_error(
        request: Request, exc: ControlPlaneError
    ) -> HTMLResponse:
        context: dict[str, Any] = {
            "status_code": exc.status_code or 502,
            "detail": exc.detail,
        }
        identity = getattr(request.state, "identity", None)
        if identity is not None:
            context.update(_page_context({}, identity))
        return templates.TemplateResponse(
            request,
            "error.html",
            context,
            status_code=502,
        )

    return app


def main() -> None:
    """Console entrypoint for the operator UI server."""
    settings = get_settings()
    uvicorn.run(create_ui_app(), host=settings.ui.host, port=settings.ui.port)


app = create_ui_app()

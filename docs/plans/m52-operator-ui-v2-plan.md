# Operator UI v2 (M52) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship M52 — role-gated operator UI v2 with live run view, approval queue v2, cost/ROI/health dashboards, trigger/queue visibility, diff viewer, global search, and onboarding wizard.

**Architecture:** Thin server-rendered FastAPI + Jinja UI over the control-plane JSON API. Identity lives in a signed HttpOnly session cookie; the forwarded bearer API key is the real gate. Pure view models (`ui/views.py`) turn API payloads into display data; `ui/client.py` is the only HTTP boundary. Two small control-plane additions: a `/search` endpoint and approval comments/delegation.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, httpx, pydantic v2, pytest, pytest-playwright (e2e marker).

## Global Constraints

- Exit gate: `pytest` passes, coverage total > 95%, `ruff check` clean, `mypy src/ tests/` strict clean.
- UI is presentation-only: never import control-plane services into `src/hiveplane/ui/`; speak JSON via `ControlPlaneClient`.
- RBAC is enforced server-side; UI hiding is usability only. The forwarded API key is the authority.
- When `settings.auth.enabled` is false, resolve an anonymous **admin** identity (matches control-plane `get_principal`).
- No SPA, no chart library, no password auth. Charts are inline SVG/CSS.
- Every new public function/model has a concise docstring (repo convention).
- Do not add comments to code unless a docstring or non-obvious invariant.
- Commit at the end of each task; do not push until the final task unless asked.

---

### Task 1: UI session settings

**Files:**
- Modify: `src/hiveplane/config.py` (`UiSettings`, ~line 186)
- Test: `tests/test_ui_config.py`

**Interfaces:**
- Produces: `Settings.ui.session_secret: str`, `Settings.ui.session_ttl_hours: int` (default 12).

- [ ] **Step 1: Write the failing test**

```python
def test_ui_session_defaults() -> None:
    from hiveplane.config import UiSettings

    settings = UiSettings()
    assert settings.session_ttl_hours == 12
    assert isinstance(settings.session_secret, str)
    assert settings.session_secret
```

Add to `tests/test_ui_config.py`. Run it and confirm it fails (attribute error).

- [ ] **Step 2: Implement**

```python
class UiSettings(BaseModel):
    """Operator UI settings (M22, M52)."""

    api_url: str = "http://localhost:8100"
    host: str = "0.0.0.0"
    port: int = Field(default=3001, ge=1, le=65535)
    session_secret: str = "dev-ui-session-secret-change-me"
    session_ttl_hours: int = Field(default=12, ge=1)
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_ui_config.py -q` → PASS.

- [ ] **Step 4: Commit**

```bash
git add src/hiveplane/config.py tests/test_ui_config.py
git commit -m "feat(m52): add UI session settings"
```

---

### Task 2: Signed session module

**Files:**
- Create: `src/hiveplane/ui/session.py`
- Test: `tests/test_m52.py`

**Interfaces:**
- Consumes: `Settings.ui.session_secret`, `Settings.ui.session_ttl_hours`.
- Produces:
  - `UiSession` (pydantic, `extra="forbid"`): `operator_id: str`, `tenant_id: str`, `role: Role`, `api_key: str`, `exp: int` (unix seconds).
  - `sign_session(session: UiSession, secret: str) -> str`
  - `verify_session(token: str, secret: str, *, now: int | None = None) -> UiSession | None` (returns `None` on bad signature, malformed payload, or expiry).
  - `SESSION_COOKIE = "hiveplane_session"`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_m52.py`)

```python
from hiveplane.ui.session import UiSession, sign_session, verify_session


def _session(exp: int = 9_999_999_999) -> UiSession:
    return UiSession(
        operator_id="alice", tenant_id="default", role=Role.ADMIN,
        api_key="hp-key-1", exp=exp,
    )


def test_session_round_trip() -> None:
    token = sign_session(_session(), "secret")
    assert verify_session(token, "secret") == _session()


def test_session_rejects_tampered_token() -> None:
    token = sign_session(_session(), "secret")
    assert verify_session(token + "x", "secret") is None
    assert verify_session(token, "other-secret") is None


def test_session_rejects_expired_and_malformed() -> None:
    assert verify_session(sign_session(_session(exp=1), "s"), "s", now=100) is None
    assert verify_session("not-a-token", "s") is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_m52.py -q -k session` → FAIL (module missing).

- [ ] **Step 3: Implement `src/hiveplane/ui/session.py`**

```python
"""Signed operator-UI session cookie (M52-01).

The cookie carries the operator identity and the API key used to reach the
control plane. The signature protects integrity only; authorization is always
the control plane's, using the forwarded key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from hiveplane.tenancy.models import Role

SESSION_COOKIE = "hiveplane_session"


class UiSession(BaseModel):
    """The operator identity and API key carried by the session cookie."""

    model_config = ConfigDict(extra="forbid")

    operator_id: str
    tenant_id: str
    role: Role
    api_key: str
    exp: int


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign_session(session: UiSession, secret: str) -> str:
    """Serialize and sign a session into a cookie-safe token."""
    raw = session.model_dump_json().encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{payload}.{_signature(payload, secret)}"


def verify_session(token: str, secret: str, *, now: int | None = None) -> UiSession | None:
    """Return the verified session, or ``None`` when invalid or expired."""
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _signature(payload, secret)):
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
        session = UiSession(**data)
    except (ValueError, TypeError):
        return None
    current = int(time.time()) if now is None else now
    if session.exp <= current:
        return None
    return session
```

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/ui/session.py tests/test_m52.py
git commit -m "feat(m52): signed UI session cookie"
```

---

### Task 3: Extend the control-plane client

**Files:**
- Modify: `src/hiveplane/ui/client.py`
- Modify: `tests/ui_fakes.py`
- Test: `tests/test_ui_client.py`

**Interfaces:**
- Produces on `ControlPlaneClient` (Protocol) and `HttpControlPlaneClient`:
  - `with_token(self, token: str | None) -> ControlPlaneClient`
  - `get_queue() -> dict[str, Any]`
  - `list_health() -> list[dict[str, Any]]`
  - `get_roi() -> dict[str, Any]`
  - `search(self, query: str, limit: int = 20) -> list[dict[str, Any]]`
  - `list_triggers() -> list[dict[str, Any]]`
  - `list_adapters() -> list[dict[str, Any]]`
  - `get_version_diff(self, name: str, from_version: int, to_version: int) -> dict[str, Any]`
  - `compare_certifications(self, before_id: str, after_id: str) -> dict[str, Any]`
  - `get_run_events(self, run_id: str) -> list[dict[str, Any]]`
  - `add_approval_comment(self, approval_id: str, author: str, text: str) -> dict[str, Any]`
  - `delegate_approval(self, approval_id: str, assignee: str, by: str) -> dict[str, Any]`
  - `list_approval_requests(self) -> list[dict[str, Any]]` is **not** added; existing `list_approvals` is reused.
- `HttpControlPlaneClient` gains `token` handling; `with_token` returns a cheap clone sharing the httpx client.

- [ ] **Step 1: Write failing tests** (`tests/test_ui_client.py`)

```python
def test_with_token_forwards_bearer() -> None: ...
def test_get_queue_hits_queue_path() -> None: ...
def test_search_passes_query_and_limit() -> None: ...
```

Use `httpx.MockTransport` to capture requests and assert `Authorization: Bearer t1` and paths `/queue`, `/search?q=...&limit=...`.

- [ ] **Step 2: Implement client methods and `with_token`**

Add to the Protocol and implement in `HttpControlPlaneClient`:

```python
def __init__(self, base_url, *, client=None, timeout=10.0, token=None):
    ...
    self._token = token

def with_token(self, token: str | None) -> HttpControlPlaneClient:
    """Return a clone that sends ``Authorization: Bearer <token>``."""
    clone = HttpControlPlaneClient.__new__(HttpControlPlaneClient)
    clone.base_url = self.base_url
    clone._client = self._client
    clone._token = token
    return clone
```

In `_request`, add `headers={"Authorization": f"Bearer {self._token}"} if self._token else None`.

New methods (paths taken from the control-plane routers):

```python
def get_queue(self): return self._request("GET", "/queue")
def list_health(self): return self._request("GET", "/health")
def get_roi(self): return self._request("GET", "/cost/roi/fleet")
def search(self, query, limit=20):
    return self._request("GET", "/search", params={"q": query, "limit": str(limit)})
def list_triggers(self): return self._request("GET", "/triggers")
def list_adapters(self): return self._request("GET", "/adapters")
def get_version_diff(self, name, from_version, to_version):
    return self._request("GET", f"/workloads/{quote(name)}/versions/diff",
                         params={"from_version": str(from_version), "to_version": str(to_version)})
def compare_certifications(self, before_id, after_id):
    return self._request("GET", f"/certifications/compare/{quote(before_id)}/{quote(after_id)}")
def get_run_events(self, run_id):
    return self._request("GET", f"/runs/{quote(run_id)}/events")
def add_approval_comment(self, approval_id, author, text):
    return self._request("POST", f"/approvals/{quote(approval_id)}/comments",
                         payload={"author": author, "text": text})
def delegate_approval(self, approval_id, assignee, by):
    return self._request("POST", f"/approvals/{quote(approval_id)}/delegate",
                         payload={"assignee": assignee, "operator": by})
```

- [ ] **Step 3: Update `tests/ui_fakes.py`**

Add `token: str | None = None` attribute, `with_token` returning `self` (recording the call), and stubs returning configurable payloads (`queue`, `health`, `roi`, `search_hits`, `triggers`, `adapters`, `version_diff`, `cert_comparison`, `run_events`). Record each call via `self._record(...)`.

- [ ] **Step 4: Run tests** → PASS, and `pytest tests/test_ui_client.py tests/test_ui_app.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/ui/client.py tests/ui_fakes.py tests/test_ui_client.py
git commit -m "feat(m52): extend control-plane client for UI v2"
```

---

### Task 4: Control-plane global search endpoint

**Files:**
- Create: `src/hiveplane/api/search.py`
- Modify: `src/hiveplane/api/app.py` (import + `include_router`)
- Test: `tests/test_search_api.py`

**Interfaces:**
- Consumes: `get_run_service`, `get_approval_service`, `get_registry_service`, `require_permission(Permission.FLEET_READ)`.
- Produces: `SearchHit(BaseModel)` fields `kind: str`, `identifier: str`, `label: str`; `GET /search?q=&limit=` → `list[SearchHit]`.

- [ ] **Step 1: Write failing test**

```python
def test_search_matches_runs_approvals_workloads(client, seeded) -> None:
    hits = client.get("/search", params={"q": "agent"}).json()
    assert {h["kind"] for h in hits} <= {"run", "approval", "workload", "attestation"}
    assert all("identifier" in h for h in hits)
```

Follow the existing API test fixture style in `tests/test_approval_api.py` / `tests/test_api_health.py` for building the app + seeded state.

- [ ] **Step 2: Implement `api/search.py`**

```python
"""Cross-entity global search for the operator UI (M52-07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import (
    get_approval_service,
    get_registry_service,
    get_run_service,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.execution.service import RunService
from hiveplane.policy.approvals import ApprovalService
from hiveplane.registry.service import RegistryService

router = APIRouter(tags=["search"])

RunDep = Annotated[RunService, Depends(get_run_service)]
ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]
RegistryDep = Annotated[RegistryService, Depends(get_registry_service)]
Reader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]


class SearchHit(BaseModel):
    """One ranked search result across runs, approvals, workloads, attestations."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    identifier: str
    label: str = ""


def _matches(query: str, *fields: str) -> bool:
    needle = query.lower()
    return any(needle in field.lower() for field in fields)


@router.get("/search", response_model=list[SearchHit])
def search(
    runs: RunDep,
    approvals: ApprovalDep,
    registry: RegistryDep,
    _: Reader,
    q: str = "",
    limit: int = 20,
) -> list[SearchHit]:
    """Search runs, approvals, workloads, and attestations by text."""
    if not q.strip():
        return []
    hits: list[SearchHit] = []
    for run in runs.list_runs():
        if _matches(q, run.id, run.workload_id, run.caller):
            hits.append(SearchHit(kind="run", identifier=run.id, label=run.workload_id))
    for approval in approvals.list():
        if _matches(q, approval.approval_id, approval.workload, approval.rule, approval.reason):
            hits.append(
                SearchHit(kind="approval", identifier=approval.approval_id, label=approval.workload)
            )
    for record in registry.list_workloads():
        if _matches(q, record.manifest.name, record.manifest.owner, record.manifest.team or ""):
            hits.append(
                SearchHit(kind="workload", identifier=record.manifest.name, label=record.manifest.owner)
            )
    return hits[: max(limit, 0)]
```

> Verify the exact `registry.list_workloads()` return type (`WorkloadRecord` with `.manifest`) while implementing; adjust attribute access to what the service returns.

- [ ] **Step 3: Register the router** in `api/app.py` (import near the other `api.*` imports; `app.include_router(search_router)` after `scheduler_router`).

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/api/search.py src/hiveplane/api/app.py tests/test_search_api.py
git commit -m "feat(m52): add global search endpoint (#403)"
```

---

### Task 5: Approval comments and delegation

**Files:**
- Modify: `src/hiveplane/core/approval.py` (`ApprovalRecord`, add `ApprovalComment`)
- Modify: `src/hiveplane/policy/approvals.py` (`ApprovalService.comment`, `.delegate`)
- Modify: `src/hiveplane/api/approvals.py` (two POST routes)
- Test: `tests/test_approvals.py`, `tests/test_approval_api.py`

**Interfaces:**
- Produces:
  - `ApprovalComment(BaseModel)`: `author: str`, `text: str`, `created_at: AwareDatetime`.
  - `ApprovalRecord` new optional fields: `delegated_to: str | None = None`, `delegated_by: str | None = None`, `comments: list[ApprovalComment] = Field(default_factory=list)`.
  - `ApprovalService.comment(approval_id, *, author, text) -> ApprovalRecord`
  - `ApprovalService.delegate(approval_id, *, assignee, operator) -> ApprovalRecord`
  - `POST /approvals/{id}/comments` body `{author, text}`; `POST /approvals/{id}/delegate` body `{assignee, operator}`; both require `Permission.APPROVE`.

- [ ] **Step 1: Write failing service tests**

```python
def test_comment_and_delegate_are_recorded(service) -> None:
    record = service.request(run_id="r1", workload="a", rule="r", reason="x")
    commented = service.comment(record.approval_id, author="alice", text="investigating")
    assert commented.comments[0].text == "investigating"
    delegated = service.delegate(record.approval_id, assignee="bob", operator="alice")
    assert delegated.delegated_to == "bob" and delegated.delegated_by == "alice"
```

- [ ] **Step 2: Implement models + service**

```python
class ApprovalComment(BaseModel):
    """An append-only comment on an approval request (M52-03)."""

    model_config = ConfigDict(extra="forbid")

    author: str = Field(min_length=1)
    text: str = Field(min_length=1)
    created_at: AwareDatetime
```

`ApprovalService.comment` loads the record, appends an `ApprovalComment(created_at=self._clock())`, saves, returns. `delegate` sets `delegated_to`/`delegated_by`, appends a comment `f"delegated to {assignee}"`, saves, returns.

- [ ] **Step 3: Implement API routes** using `ApprovalDep` + `Permission.APPROVE`; request bodies `ApprovalCommentRequest{author, text}` and `DelegationRequest{assignee, operator}`.

- [ ] **Step 4: Add API tests** asserting 200 + persisted fields, and 403 for a viewer-role principal (if auth enabled in the fixture).

- [ ] **Step 5: Run** `pytest tests/test_approvals.py tests/test_approval_api.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/hiveplane/core/approval.py src/hiveplane/policy/approvals.py src/hiveplane/api/approvals.py tests/test_approvals.py tests/test_approval_api.py
git commit -m "feat(m52): approval comments and delegation (#399)"
```

---

### Task 6: UI identity resolution and login/logout

**Files:**
- Create: `src/hiveplane/ui/deps.py`
- Modify: `src/hiveplane/ui/app.py`
- Create: `src/hiveplane/ui/templates/login.html`
- Modify: `src/hiveplane/ui/templates/base.html`
- Test: `tests/test_m52.py`, `tests/test_ui_app.py`

**Interfaces:**
- Produces:
  - `resolve_identity(request: Request) -> UiIdentity` (reads cookie via `verify_session`; anonymous admin when auth disabled; `None`-able? No — returns anonymous admin so existing tests pass).
  - `client_for(request: Request) -> ControlPlaneClient` (`request.app.state.control_plane.with_token(session.api_key if session else None)`).
  - `require_ui_permission(permission)` FastAPI dependency → `UiIdentity`, raising 403.
  - Routes `GET /login`, `POST /login` (form `api_key`, optional `tenant_id`), `POST /logout`.

- [ ] **Step 1: Write failing route tests**

```python
def test_login_sets_session_cookie() -> None:
    fake = FakeControlPlaneClient()
    fake.whoami = {"operator_id": "alice", "tenant_id": "default", "role": "approver",
                   "method": "api_key", "scopes": []}
    client = TestClient(create_ui_app(client=fake))
    response = client.post("/login", data={"api_key": "k1"}, follow_redirects=False)
    assert response.status_code == 303
    assert "hiveplane_session" in response.cookies


def test_viewer_is_denied_approve() -> None:
    fake = FakeControlPlaneClient()
    client = TestClient(create_ui_app(client=fake))
    # sign a viewer session directly with the configured secret
    ...
    response = client.post("/approvals/a1/approve", data={"operator": "v"})
    assert response.status_code == 403
```

Add `whoami` to `FakeControlPlaneClient` (records `whoami`, returns configured identity or raises `ControlPlaneError(401, ...)`). Add a helper in `tests/ui_fakes.py` to mint a session cookie.

- [ ] **Step 2: Implement `ui/deps.py`**

```python
"""UI identity, per-request client, and RBAC dependency (M52-01)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from hiveplane.auth.models import Permission
from hiveplane.config import get_settings
from hiveplane.tenancy.models import Role
from hiveplane.ui.client import ControlPlaneClient
from hiveplane.ui.rbac import UiIdentity, can
from hiveplane.ui.session import SESSION_COOKIE, verify_session


def resolve_identity(request: Request) -> UiIdentity:
    """Resolve the operator identity from the signed session cookie."""
    settings = get_settings()
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = verify_session(token, settings.ui.session_secret)
        if session is not None:
            request.state.api_key = session.api_key
            return UiIdentity(role=session.role, operator_id=session.operator_id)
    if not settings.auth.enabled:
        return UiIdentity(role=Role.ADMIN)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")


def client_for(request: Request) -> ControlPlaneClient:
    """Return a control-plane client carrying the session's API key, if any."""
    base: ControlPlaneClient = request.app.state.control_plane
    return base.with_token(getattr(request.state, "api_key", None))


IdentityDep = Annotated[UiIdentity, Depends(resolve_identity)]


def require_ui_permission(permission: Permission):
    """Build a dependency that refuses the request without ``permission``."""

    def _dependency(identity: IdentityDep) -> UiIdentity:
        if not can(identity, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return identity

    return _dependency
```

> Circular-import note: `ui.deps` must not be imported by `ui.rbac`. Keep `rbac.py` as-is.

- [ ] **Step 3: Wire routes in `ui/app.py`**

- Add `@app.middleware("http")` or a per-route `identity = resolve_identity(request)`; simplest is to call `resolve_identity(request)` at the top of each route and store on `app.state`-independent `request.state`.
- `GET /login` renders `login.html`; `POST /login` calls `client_for(request).whoami()` inside try/except `ControlPlaneError`; on success set cookie `response.set_cookie(SESSION_COOKIE, sign_session(...), httponly=True, samesite="lax", max_age=ttl)`. Requires a `whoami()` client method — add `whoami()` to Protocol/HTTP client/Fake.
- `POST /logout` deletes the cookie.
- Apply `require_ui_permission` to the privileged routes added in later tasks; for existing `approve`/`deny` add `_: Annotated[UiIdentity, Depends(require_ui_permission(Permission.APPROVE))]`.
- Templates: pass `identity` + `visible_actions(identity)` to every `TemplateResponse`; `base.html` nav renders gated links and a login/logout control.

- [ ] **Step 4: Run** `pytest tests/test_m52.py tests/test_ui_app.py tests/test_ui_views.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/ui/deps.py src/hiveplane/ui/app.py src/hiveplane/ui/templates/login.html src/hiveplane/ui/templates/base.html tests/ui_fakes.py tests/test_m52.py
git commit -m "feat(m52): UI login and RBAC gating (#397)"
```

---

### Task 7: Live run view via SSE

**Files:**
- Create: `src/hiveplane/ui/stream.py`
- Modify: `src/hiveplane/ui/app.py`, `src/hiveplane/ui/templates/run_detail.html`
- Test: `tests/test_m52.py`

**Interfaces:**
- Produces: `run_event_frames(events: Sequence[dict], *, seen: int) -> Iterator[str]` (pure; `event: run\ndata: <json>\n\n` for events with `sequence >= seen`), and `GET /runs/{run_id}/stream` returning `StreamingResponse(media_type="text/event-stream")`.
- `settings.ui.stream_max_seconds` not required: use a module constant `STREAM_MAX_SECONDS = 300` and `STREAM_POLL_SECONDS = 1.0`.

- [ ] **Step 1: Write failing pure test**

```python
def test_run_event_frames_only_new_events() -> None:
    events = [{"sequence": 1, "type": "state_change"}, {"sequence": 2, "type": "usage"}]
    frames = list(run_event_frames(events, seen=1))
    assert len(frames) == 1
    assert '"sequence": 2' in frames[0]
    assert frames[0].startswith("event: run")
```

- [ ] **Step 2: Implement `ui/stream.py`**

```python
"""Server-Sent Events for the live run view (M52-02)."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

STREAM_MAX_SECONDS = 300.0
STREAM_POLL_SECONDS = 1.0


def run_event_frames(events: Sequence[dict[str, Any]], *, seen: int) -> Iterator[str]:
    """Yield SSE frames for events beyond ``seen``, ordered by sequence."""
    ordered = sorted(events, key=lambda event: int(event.get("sequence", 0)))
    for event in ordered:
        if int(event.get("sequence", 0)) <= seen:
            continue
        yield f"event: run\ndata: {json.dumps(event)}\n\n"
```

- [ ] **Step 3: Implement the SSE route**

`GET /runs/{run_id}/stream` is an `async def` that loops until terminal/`STREAM_MAX_SECONDS`, calling `client.get_run_events(run_id)` via `asyncio.to_thread`, emitting new frames and a `: heartbeat\n\n` comment between polls, then `event: end\ndata: {}\n\n`. Read terminal state via `client.get_run(run_id)`.

- [ ] **Step 4: Template** — add `<div id="run-timeline" data-run-id="{{ view.run_id }}">` and a small inline `<script>` that opens `new EventSource('/runs/{{ view.run_id }}/stream')`, appends each frame's `summary` to the timeline list, and closes on `end`. Keep the statically rendered entries as the no-JS fallback.

- [ ] **Step 5: Route test** with `TestClient` — assert `GET /runs/r1/stream` returns 200 and contains `event: run` for a fake with two events (test client will consume a finite stream; have the fake report a terminal run so the loop ends immediately).

- [ ] **Step 6: Run** `pytest tests/test_m52.py tests/test_ui_app.py -q` → PASS.

- [ ] **Step 7: Commit**

```bash
git add src/hiveplane/ui/stream.py src/hiveplane/ui/app.py src/hiveplane/ui/templates/run_detail.html tests/test_m52.py
git commit -m "feat(m52): live run view SSE stream (#398)"
```

---

### Task 8: Approval queue v2

**Files:**
- Modify: `src/hiveplane/ui/app.py`, `src/hiveplane/ui/templates/approvals.html`
- Test: `tests/test_ui_app.py`

**Interfaces:**
- Produces routes: `POST /approvals/bulk` (form `decision`, repeated `approval_id`, `operator`), `POST /approvals/{id}/comment` (form `author`, `text`), `POST /approvals/{id}/delegate` (form `assignee`, `operator`). All require `APPROVE`.

- [ ] **Step 1: Failing test**

```python
def test_bulk_approve_calls_client_per_id() -> None:
    fake = _approvals_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    response = client.post(
        "/approvals/bulk",
        data=[("decision", "approve"), ("approval_id", "a1"), ("approval_id", "a2"), ("operator", "alice")],
    )
    assert response.status_code == 303
    assert ("approve", ("a1", "alice", None)) in fake.calls
    assert ("approve", ("a2", "alice", None)) in fake.calls
```

- [ ] **Step 2: Implement routes** looping `list[str] = Form(...)`; collect per-id errors into a flash query param. `comment`/`delegate` call the new client methods.

- [ ] **Step 3: Template** — add multi-select checkboxes + a bulk action bar, a comment form per pending row, and a delegate form; render `comments`/`delegated_to` when present.

- [ ] **Step 4: Run** `pytest tests/test_ui_app.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/ui/app.py src/hiveplane/ui/templates/approvals.html tests/test_ui_app.py
git commit -m "feat(m52): approval queue v2 bulk/comment/delegate (#399)"
```

---

### Task 9: Cost, ROI, and health screens

**Files:**
- Modify: `src/hiveplane/ui/app.py`, `src/hiveplane/ui/views.py`
- Create: `src/hiveplane/ui/templates/cost.html`, `roi.html`, `health.html`
- Test: `tests/test_m52.py`, `tests/test_ui_app.py`

**Interfaces:**
- Produces: `build_health_from_api(workloads: list[dict]) -> HealthView` mapping `WorkloadHealth` fields (`failure_rate` → `success_rate = 1 - failure_rate`; `error_budget_remaining` from the availability objective's remaining budget when present, else `0.0`) and `build_diff`-style mappers for later tasks.
- Routes `GET /cost`, `GET /roi`, `GET /health`.

- [ ] **Step 1: Failing tests** for `build_health_from_api` and route rendering.

- [ ] **Step 2: Implement mapper + routes + templates** (inline SVG bars for spend; tables otherwise). Reuse `build_spend`, `build_roi`, `build_health` from `views.py`.

- [ ] **Step 3: Run** → PASS. Commit `feat(m52): cost, ROI, and health dashboards (#400)`.

---

### Task 10: Trigger log and queue visualizer

**Files:**
- Modify: `src/hiveplane/ui/app.py`
- Create: `src/hiveplane/ui/templates/triggers.html`, `queue.html`
- Test: `tests/test_ui_app.py`

**Interfaces:**
- Routes `GET /triggers` (calls `list_triggers`) and `GET /queue` (`get_queue` → `build_queue`). `build_queue` already accepts a snapshot dict; map `WaitingReason` (`task_id`, `workload`, `qos`, `priority`, `reason`) directly.

- [ ] **Step 1: Failing tests** asserting trigger ids and queue depth/reason render.
- [ ] **Step 2: Implement routes + templates.**
- [ ] **Step 3: Run** → PASS. Commit `feat(m52): trigger log and queue visualizer (#401)`.

---

### Task 11: Diff viewer

**Files:**
- Modify: `src/hiveplane/ui/views.py`
- Modify: `src/hiveplane/ui/app.py`
- Create: `src/hiveplane/ui/templates/diff.html`
- Test: `tests/test_m52.py`, `tests/test_ui_app.py`

**Interfaces:**
- Produces pure mappers:
  - `diff_from_version_diff(payload: dict) -> DiffView` — one entry per `changed_fields` (`field=<name>`, `before="—"`, `after="changed"`), title `f"{workload} v{from}→v{to}"`.
  - `diff_from_regression(payload: dict) -> DiffView` — one entry per `regressed`/`improved` task (`field=task id`, before/after pass state from `TaskDelta`), title `f"{workload_id} regression"`.
  - `diff_from_runs(before: dict, after: dict) -> DiffView` — compare the two stories' top-level `state`, `cost_usd`, `model_identity` fields.
- Route `GET /diff?kind=&...` dispatches on `kind` (`version`|`regression`|`runs`).

- [ ] **Step 1: Failing tests** for each mapper (fixed payloads).
- [ ] **Step 2: Implement mappers + route + template.**
- [ ] **Step 3: Run** → PASS. Commit `feat(m52): diff viewer (#402)`.

---

### Task 12: Global search screen

**Files:**
- Modify: `src/hiveplane/ui/app.py`
- Create: `src/hiveplane/ui/templates/search.html`
- Test: `tests/test_ui_app.py`

**Interfaces:**
- Route `GET /search?q=` → `build_search(query, client.search(query))`; renders hits with links by `kind` (run → `/runs/{id}`, approval → `/approvals`, workload → `/workloads`).

- [ ] **Step 1: Failing test** — fake `search_hits` populated; assert hit label + `href` present.
- [ ] **Step 2: Implement route + template.**
- [ ] **Step 3: Run** → PASS. Commit `feat(m52): global search screen (#403)`.

---

### Task 13: Onboarding wizard

**Files:**
- Modify: `src/hiveplane/ui/app.py`
- Create: `src/hiveplane/ui/templates/onboarding.html`
- Test: `tests/test_ui_app.py`

**Interfaces:**
- Route `GET /onboarding` derives `done` from live state:
  - `connect` when `client.list_adapters()` is non-empty,
  - `register` when `client.list_workloads()` is non-empty,
  - `certify` when any `client.list_certifications()` has status `certified`,
  - `trigger` when `client.list_triggers()` is non-empty.
- Renders `build_onboarding(done)`.

- [ ] **Step 1: Failing test** — empty fake → 0 steps done; populated fake → all 4 done.
- [ ] **Step 2: Implement route + template** (progress bar + per-step links).
- [ ] **Step 3: Run** → PASS. Commit `feat(m52): onboarding wizard (#404)`.

---

### Task 14: Browser E2E coverage

**Files:**
- Modify: `tests/e2e/conftest.py` (seed adapters/cert/trigger if needed)
- Modify: `tests/e2e/live_stack.py` (add any seeded ids)
- Modify: `tests/e2e/test_ui_e2e.py`
- Test: `tests/e2e/test_ui_e2e.py`

**Interfaces:**
- Reuse the `LiveStack` fixture. Add tests: login flow (auth disabled → anonymous admin, nav visible), onboarding page renders 4 steps, queue page shows depth, health/ROI/search/diff pages render headings.

- [ ] **Step 1: Write the Playwright tests** using `page.goto(f"{stack.ui_url}/...")` + `expect(...).to_be_visible()`.
- [ ] **Step 2: Run** `python -m playwright install chromium` then `pytest tests/e2e -m e2e -q` → PASS.
- [ ] **Step 3: Commit** `test(m52): operator UI v2 browser e2e (#405)`.

---

### Task 15: Exit gate and docs

**Files:**
- Modify: `docs/design/operator-ui-v2-design.md` (status → implemented), `docs/design/operator-experience-design.md`, `USER_GUIDE.md`, `CHANGELOG.md`
- Modify: `docs/wbs/v0.2.0/wbs-v0.2.0-part14-delivery-ui.md` (check off M52 items, status note)

- [ ] **Step 1: Full verification**

```bash
pytest -q
pytest --cov=src/hiveplane --cov-report=term-missing -q
ruff check
mypy src/ tests/
```

Expected: all pass; coverage > 95%.

- [ ] **Step 2: Update docs** — mark M52 implemented, tick acceptance criteria, record the commands above in the status note (match the M51 note format).

- [ ] **Step 3: Commit**

```bash
git add docs USER_GUIDE.md CHANGELOG.md
git commit -m "docs(m52): mark operator UI v2 complete"
```

---

## Self-Review

- **Spec coverage:** #397 (T6), #398 (T7), #399 (T5+T8), #400 (T9), #401 (T10), #402 (T11), #403 (T4+T12), #404 (T13), #405 (T14). Session/RBAC (T1–T2, T6), search backend (T4), approval backend (T5) cover the spec's backend additions.
- **Placeholder scan:** T9–T14 describe routes/templates at a slightly higher level than T1–T8; implementation details (exact template markup) are deliberately left to the executor since HTML has no testable interface beyond text assertions. No TBD/TODO remains.
- **Type consistency:** `with_token`, `UiIdentity`, `UiSession`, `ApprovalComment`, `SearchHit` are defined once and referenced consistently. `build_health_from_api` is introduced because the control-plane `WorkloadHealth` shape differs from the `build_health` input.
- **Known follow-up:** exact `RegistryService.list_workloads()` return attributes must be confirmed during T4; the plan flags this inline.

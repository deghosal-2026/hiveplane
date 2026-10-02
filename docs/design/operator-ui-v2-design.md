# D39: Operator UI v2 Design

> Status: M52 implemented; v0.2.0 in progress

**Milestones:** M52 · **Extends:** D9 (Operator UI), D36 (Operator Experience)

## Problem

D9 shipped a minimal, read-mostly UI over the control-plane API: a fleet list, run detail, approval queue, certification dashboard, and spend view. M51 made results and approvals reach operators wherever they are. M52 must make the UI itself tell the whole fleet story and let operators act safely from it: a live run view, a role-gated login, an approval queue that supports bulk and delegated decisions with comments, cost/ROI/health dashboards, trigger and queue visibility, diff viewing, global search, and an onboarding wizard. Every operator action must be role-gated server-side and audited.

The UI is and stays a **presentation layer**. It never imports control-plane services; it speaks JSON over the API. This keeps RBAC, policy, and audit in one place and lets the UI evolve without leaking framework concerns into core.

## Overview

```
                         Operator UI v2 (FastAPI + Jinja, no SPA)
      ┌───────────────────────────────────────────────────────────────────┐
      │ session.py (signed cookie)   rbac.py (visible actions)            │
      │ app.py (routes)              views.py (pure view models)          │
      └───────────────┬───────────────────────────────────────────────────┘
                      │ HTTP + Bearer API key (forwarded)
                      ▼
      ┌───────────────────────────────────────────────────────────────────┐
      │ Control-plane API                                                 │
      │  runs · approvals · certifications · spend · cost/roi · health    │
      │  triggers · queue · versions/diff · search (new)                  │
      └───────────────────────────────────────────────────────────────────┘
```

Two boundaries are load-bearing:

1. **Session/RBAC** — identity resolution and action gating live in `ui/session.py` + `ui/rbac.py`; the UI hides controls for usability, but the control plane authenticates the forwarded API key and is the real gate.
2. **HTTP client** — `ui/client.py` is the sole control-plane boundary, so routes and view models can be tested against a fake client.

## Design

### Session and identity

A signed, `HttpOnly`, `SameSite=Lax` cookie holds `{operator_id, tenant_id, role, api_key, exp}`. The signature is an HMAC (stdlib `hmac` + base64-encoded JSON) over a secret from the new `ui.session_secret` setting, and `exp` is a TTL (default 12 hours) after which the cookie verifies as absent and the operator is sent back to `/login`. The cookie's payload is never trusted for authorization on the control plane: the UI forwards `api_key` as a `Bearer` token, and the control plane authorizes each call. The UI's own gate is a second, usability-only layer.

When `settings.auth.enabled` is false the UI resolves an **anonymous admin** identity, exactly matching the control plane's `get_principal` behavior, so local development and the existing E2E stack keep working unchanged.

`GET /login` renders a form (API key, optional tenant); `POST /login` verifies the key by calling the control plane's `GET /auth/whoami` with the supplied Bearer token. On success the session cookie is signed and set; on failure the form re-renders with a 401 banner. `POST /logout` clears the cookie. An **API key**, not a password, is the credential: it is the artifact M45 already issues, scopes, and revokes.

### RBAC gating

`ui/rbac.py` maps a role to the privileged controls it may see (`approve`, `promote`, `kill_switch`, `secrets_manage`) using the shared `hiveplane.auth.rbac.has_permission`. The same mapping backs a `require_ui_permission(permission)` dependency applied to every privileged route. The nav and screen templates render controls only when `visible_actions(identity)` allows them; a request that bypasses the hidden control is refused server-side with 403 and audited by the control plane. A viewer therefore sees no approve/promote/kill-switch controls, and an approver sees approve but not promote or kill-switch.

### Live run view and timeline (#398)

The run detail screen renders the run's story as a timeline (the existing `build_run_detail` view model) and subscribes to `GET /runs/{run_id}/stream`, a Server-Sent Events endpoint. The UI endpoint polls the control plane's `GET /runs/{id}/events` server-side, emits an `event: run` frame for each new event, sends comment heartbeats while idle, and closes when the run reaches a terminal state (`completed`, `failed`, `cancelled`) or a maximum duration elapses. Browsers without `EventSource` fall back to the statically rendered story, so the page is always useful.

The stream is read-only and unprivileged beyond viewing the run; it adds no new control-plane mutation surface.

### Approval queue v2 (#399)

The queue groups pending and resolved approvals and adds:

- **Bulk actions** — a multi-select form posts a set of approval ids to `/approvals/bulk`, which loops the existing per-approval approve/deny path through the client. Every decision is a discrete, attributed, audited call; there is no batch-mutation shortcut in the control plane.
- **Comments** — `POST /approvals/{id}/comments` appends an `ApprovalComment` (author, text, timestamp) to the record. Comments are visible on the queue and are append-only.
- **Delegation** — `POST /approvals/{id}/delegate` records `delegated_to`/`delegated_by` on the record and appends a comment, so an approver can hand a decision to a teammate without losing the audit trail. Delegation changes who is expected to act; it does not transfer authority — the delegate still needs `approve`.

`ApprovalRecord` gains optional `delegated_to`, `delegated_by`, and `comments` fields (defaults preserve every existing record and test).

### Cost, ROI, and health dashboards (#400)

Three read-only screens reuse existing control-plane endpoints:

- `/cost` — cost explorer: spend by workload/team and over time, rendered as inline SVG/CSS bars (no chart library).
- `/roi` — the fleet ROI report with flagged expensive-low-value rows and their evidence.
- `/health` — per-workload health, success rates, and error-budget remaining, plus SLO burn where available.

All three are backed by pure view models (`build_spend`, `build_roi`, `build_health`) so numbers render identically regardless of transport.

### Triggers and queue visibility (#401)

- `/triggers` — trigger log: configured triggers and their recent events, with enable/disable and DLQ replay links.
- `/queue` — queue visualizer: depth, QoS and priority breakdown, waiting items with reasons, and running load per workload, from the scheduler's `GET /queue` snapshot.

### Diff viewer (#402)

There is no first-class run-to-run diff endpoint. The viewer composes the sources that exist:

- **Certification regression diff** — `GET /certifications/compare/{before}/{after}`.
- **Workload version diff** — `GET /workloads/{name}/versions/diff?from_version=&to_version=`.
- **Run-to-run comparison** — a field-by-field comparison of two runs' story/usage, computed **in the UI view model** (`build_diff`) from the two stories. This keeps the comparison presentation-only and avoids inventing a control-plane concept before artifacts land in M53–M54.

`build_diff` accepts any of these payloads and renders a uniform, field-level table.

### Global search (#403)

A new control-plane endpoint `GET /search?q=&limit=` (new `api/search.py`) performs cross-entity text search over:

- runs (id, workload, caller),
- approvals (id, workload, rule, reason),
- workloads (name, owner, team).

It returns a typed, ranked list of hits (`kind`, `identifier`, `label`). The UI's `/search` route renders them with links to the relevant screens. Search is `FLEET_READ`-gated like other reads and never returns secret material. Keeping search server-side keeps the UI thin and lets the query grow with the data model. Attestation and artifact search is deferred until those concepts land in M53–M54.

### Onboarding wizard (#404)

`/onboarding` walks a new operator through **connect a model → register a workload → certify → fire the first trigger**, deriving each step's completion from live state rather than stored progress:

- **connect** — at least one runtime adapter is available (`GET /adapters`).
- **register** — at least one workload is registered (`GET /workloads`).
- **certify** — at least one certification has passed (`GET /certifications`).
- **trigger** — at least one trigger has fired at least one run (`GET /triggers` + events/runs).

`build_onboarding(done)` (already landed) renders the four steps with links, so completion is always reproducible from the fleet's actual state.

### Error handling

Control-plane failures surface as the existing `error.html` 502 page. A missing run renders `not_found.html` with 404. Login failures render 401 on the login form. RBAC denials render 403. Every mutation route flashes success or failure back to its screen via the existing redirect-with-query pattern.

### Testing

- **Unit** — `tests/test_m52.py` extends to the remaining view models and adds session sign/verify tests (tampered cookie rejected, expired/absent cookie → anonymous or redirect).
- **Route** — `create_ui_app(FakeClient)` tests assert each route renders, that a viewer gets 403 on privileged POSTs while an approver succeeds, and that the SSE endpoint emits expected frames from a scripted event list.
- **Browser** — `tests/e2e/test_ui_e2e.py` gains login, onboarding wizard, queue, health, ROI, search, and diff flows, reusing the `LiveStack` fixture. These run under the `e2e` marker via `make test-e2e`.

The M52 exit gate requires `pytest`, coverage > 95%, `ruff check`, `mypy src/ tests/`, updated docs, and a commit/push.

> **Status:** M52 complete. The operator UI v2 ships on the existing server-rendered FastAPI + Jinja presentation layer (`hiveplane.ui`), adding a signed-cookie session (`ui/session.py`), role-based action visibility (`ui/rbac.py`), and the new screens **login/logout**, **live run stream** (`GET /runs/{id}/stream`, SSE), **approval queue v2** (bulk, comments, delegation), **cost/ROI/health**, **trigger log**, **queue visualizer**, **diff viewer**, **global search**, and **onboarding wizard**. A new control-plane endpoint `GET /search` (`api/search.py`) backs cross-entity text search over runs, approvals, and workloads; attestation and artifact search is deferred until those concepts land in M53–M54. Routes: `GET /login`, `POST /login|/logout`, `GET/POST /approvals{,/bulk,/{id}/comment,/{id}/delegate}`, `GET /cost|/roi|/health|/triggers|/queue|/search|/onboarding|/diff`, and `GET /runs/{id}/stream`. Issues #397–#405 closed; tests: 2237 unit/route pass with Postgres plus 17 Playwright e2e; coverage 95.18% (with database), ruff and mypy strict clean.

## Non-goals

- No SPA framework, no client-side routing, no charting library.
- No run-to-run diff endpoint (presentation-only comparison for now).
- No password authentication; API keys remain the credential.
- No new authorization semantics in the UI — roles and permissions are M45's.

## Risks

- **Interactive/privileged UI is a security surface.** RBAC must be enforced server-side on every mutating route; the UI gate is usability only. The forwarded API key and the control plane's `require_permission` remain the authority.
- **SSE resource use.** The stream endpoint must close on terminal state and cap duration; otherwise abandoned tabs leak pollers.
- **Playwright flakiness.** Live-stack browser tests are timing-sensitive; assert on visible text, not timing, and keep the seed scenario deterministic.
- **Search scope creep.** The endpoint searches the entities that exist today; artifacts/log corpora join when those concepts land.

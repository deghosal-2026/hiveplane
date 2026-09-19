# M22: Operator UI — Implementation Design

> Status: approved (implementation spec)
> Milestone: M22 · Version: v0.1.0
> Issues: [#54](https://github.com/deghosal-2026/hiveplane/issues/54) (fleet list, run detail),
> [#55](https://github.com/deghosal-2026/hiveplane/issues/55) (approval queue, certification dashboard, spend view)
> Related: [Operator UI Design](operator-ui-design.md) (product design, D9),
> [WBS Part 11](../wbs/v0.1.0/wbs-v0.1.0-part11-cli-operator-ui.md)

## Goal

Give operators one surface to see the fleet, inspect a run, resolve approvals, review
certification, and review spend — and to act, not just look. Deliver the five v0.1.0
screens from D9: **fleet list**, **run detail**, **approval queue**, **certification
dashboard**, and **spend view**.

## Decisions

These were settled during design review and constrain the work:

1. **Server-rendered Python UI, not React for v0.1.0.** The repository is pure Python with
   strict `pytest` (>95% coverage), `ruff`, and `mypy --strict` gates and no Node toolchain.
   A React SPA would sit outside every gate and require new CI. The D9 "React" target is
   deferred; the UI is built with Jinja2 templates served by FastAPI. See
   [Future Enhancements](#future-enhancements).
2. **The UI is an HTTP client of the control-plane REST API.** It does not import or call
   the service layer in-process. This preserves the API boundary and keeps a future React
   (or other) client on the same contract.
3. **Actions exposed in M22:** approve/deny approvals, plus pause/resume/stop runs from run
   detail. "Certify now" is out of scope (certification runs can be slow/blocking).
4. **Only one backend addition:** `GET /spend`. Every other screen is assembled from
   existing endpoints.

### Data-availability findings

- `CatalogEntry.failure_count` / `last_run_at` / `last_failure_at` are defined but never
  populated (`RegistryService` only maintains `production_runs_survived`). The fleet list
  therefore derives state counts, recent failures, and last-run from `GET /runs`, not from
  the catalog.
- There is no spend/budget read endpoint. `BudgetStore.list_attributions()` holds the data,
  so one new endpoint is required.
- `GET /runs/{run_id}/story` already returns the full execution story (state timeline, tool
  calls, model calls, cost, sandbox, deliveries, approvals, trace link). Run detail is a
  single call.
- `GET /certifications` returns `CertificationRecord`s carrying status, timestamp,
  `eval_summary.pass_rate`, target context, and attestation — enough to derive the
  certification dashboard in the UI.

## Architecture

```
                    HTTP                      HTTP
 operator -> +-----------+   GET/POST   +----------------+
             |  UI app   | -----------> | control plane  |
             | (SSR)     |              | FastAPI API    |
             | :3000     |              | :8000          |
             +-----------+              +----------------+
              Jinja2 templates            registry / runs / policy / budget
```

- New package `src/hiveplane/ui/` implements a standalone FastAPI application.
- It is configured with the control-plane base URL (`HIVEPLANE_UI__API_URL`,
  default `http://localhost:8000`).
- New Docker Compose service `ui` builds the same image, runs the UI server on port 3000,
  and depends on the `api` service.
- New runtime dependencies: `jinja2>=3.1` (ships `py.typed`, mypy-strict safe) and
  `httpx>=0.27`.

## Backend addition: `GET /spend`

**Router:** `src/hiveplane/api/spend.py` (registered in `create_app`).

**Models:** added to `src/hiveplane/budget/models.py`:

```python
class SpendByWorkload(BaseModel):
    workload: str
    team: str | None = None
    total_usd: float
    run_count: int

class SpendByTeam(BaseModel):
    team: str
    total_usd: float
    run_count: int

class SpendSummary(BaseModel):
    by_workload: list[SpendByWorkload]
    by_team: list[SpendByTeam]
```

**Behaviour:** aggregate `BudgetStore.list_attributions()` by workload and by team. Team-less
attributions appear under workload with `team=None` and are excluded from `by_team`.
`run_count` counts distinct run ids. Totals are all-time from the store (the store is
in-memory today; Postgres attribution aggregation is a future enhancement).

**Wiring:** expose `app.state.budget_store` in `create_app` and add a
`get_budget_store` dependency in `api/deps.py`.

## UI application

### `ui/client.py` — control-plane client

- `ControlPlaneClient` — a `Protocol` covering the API calls the UI makes:
  - `list_workloads()`, `get_workload(name)`
  - `list_runs(workload=None, state=None)`, `get_run(run_id)`, `get_story(run_id)`
  - `list_approvals(status=None, workload=None)`
  - `approve(approval_id, operator, reason=None)`,
    `deny(approval_id, operator, reason=None)`
  - `pause(run_id)`, `resume(run_id)`, `stop(run_id)`
  - `list_certifications(workload=None, status=None)`
  - `get_spend()`
- `HttpControlPlaneClient(base_url, ...)` — sync `httpx.Client` implementation.
- Raises `ControlPlaneError(status_code, detail)` on non-2xx responses.
- Testable with `httpx.MockTransport`; no sockets required.

### `ui/views.py` — pure view models

No HTTP, no framework. Functions take parsed API data and return display models, so they are
unit-testable in isolation:

- `build_fleet(workloads, runs, spend) -> FleetView`
  - per workload: owner, team, cert status, state counts (queued/running/paused/completed/
    failed/cancelled), recent failures (failed/cancelled runs), last run time, budget burn
    (from spend total)
  - fleet totals: workload count, cert-status counts, total spend
- `build_run_detail(story) -> RunDetailView`
  - ordered timeline entries, cost, model identity, sandbox state, trace link, approvals
- `build_cert_dashboard(records) -> CertDashboardView`
  - status counts (`uncertified` / `provisional` / `certified` / `quarantined`)
  - pass-rate trend per workload (ordered by timestamp)
  - last-certified timestamp per workload
  - quarantine history (records with status `quarantined`)
- `build_spend(spend) -> SpendView`
  - totals by workload and by team, fleet total

### `ui/app.py` — routes

| Method | Path | Screen / action |
|--------|------|-----------------|
| GET | `/` | Fleet list |
| GET | `/runs/{run_id}` | Run detail |
| POST | `/runs/{run_id}/pause` | Pause run, redirect back |
| POST | `/runs/{run_id}/resume` | Resume run, redirect back |
| POST | `/runs/{run_id}/stop` | Stop run, redirect back |
| GET | `/approvals` | Approval queue |
| POST | `/approvals/{approval_id}/approve` | Approve (operator, reason), redirect |
| POST | `/approvals/{approval_id}/deny` | Deny (operator, reason), redirect |
| GET | `/certifications` | Certification dashboard |
| GET | `/spend` | Spend view |
| GET | `/healthz` | Liveness |

- `create_ui_app(client: ControlPlaneClient | None = None) -> FastAPI` injects the client for
  tests; when omitted it builds an `HttpControlPlaneClient` from `Settings.ui.api_url`.
- Jinja2 templates live in `ui/templates/`: `base.html`, `fleet.html`, `run_detail.html`,
  `approvals.html`, `certifications.html`, `spend.html`.
- Templates use no client-side framework; forms use native HTML `POST`. Styling is a single
  embedded stylesheet (no build step).
- `main()` entrypoint runs uvicorn; exposed as the `hiveplane-ui` console script.

### Configuration

Add to `src/hiveplane/config.py`:

```python
class UiSettings(BaseModel):
    api_url: str = "http://localhost:8000"
```

and `ui: UiSettings = Field(default_factory=UiSettings)` on `Settings`.

## Error handling

- `ControlPlaneError` from a read call → render an error page with the status and detail,
  HTTP 502.
- Unknown run/workload (404 from the API) → friendly not-found page, HTTP 404.
- Failed action (e.g. approval already decided, run not paused) → redirect back to the
  originating screen with an error banner; success shows a success banner.
- The UI never exposes raw tracebacks.

## Testing

New tests (all part of the existing `pytest`/coverage/mypy gates):

- `tests/test_spend_api.py` — `/spend` aggregation: by workload, by team, team-less
  attribution, empty store, multiple runs.
- `tests/test_ui_contract_api.py` — consolidated API contract suite for the endpoints the UI
  consumes (workloads, runs + story, interventions, approvals + decisions, certifications),
  so UI breakage is caught at the API layer.
- `tests/test_ui_client.py` — `HttpControlPlaneClient` against `httpx.MockTransport`:
  success parsing, each resource, and `ControlPlaneError` on 4xx/5xx.
- `tests/test_ui_views.py` — pure view-model builders: state counts, failures, trends,
  last-certified, quarantine history, spend totals, empty inputs.
- `tests/test_ui_app.py` — route rendering and actions via `starlette.testclient.TestClient`
  with a fake in-memory `ControlPlaneClient`: each screen renders, approve/deny and
  pause/resume/stop issue the right client calls, error paths render banners/pages.

End-to-end browser tests (`tests/e2e/test_ui_e2e.py`) use **pytest-playwright** (Python, no
Node toolchain) against the real control plane and UI server started in-process on ephemeral
ports. E2E tests carry the `e2e` marker: plain `pytest` runs the unit suite only;
`make test-e2e` (and CI) run the browser flows — fleet list, run detail + interventions,
approval approve/deny, certification dashboard, spend view, 404 and 502 pages.

Exit gate for M22 (from the WBS):

- `pytest` — all tests pass
- `pytest --cov=src/hiveplane --cov-report=term-missing` — total > 95%
- `ruff check` — clean
- `mypy src/ tests/` — strict clean
- docs updated, all M22 issues verified and closed, changes committed and pushed

## Documentation and deliverables

- `docs/design/operator-ui-design.md` — note that v0.1.0 ships a Python SSR UI and that the
  React operator UI is deferred (stack section).
- `docs/design/operator-ui-implementation.md` — this document.
- `README.md` — document the UI service, port, and `HIVEPLANE_UI__API_URL`; adjust the
  "UI: React" stack note.
- `docs/USER_GUIDE.md` — add an "Operator UI" section: start the UI, browse the five screens,
  resolve approvals and intervene on runs.
- `docs/wbs/v0.1.0/wbs-v0.1.0-part11-cli-operator-ui.md` — tick M22 checkboxes.
- `docs/wbs/v0.1.0/wbs-v0.1.0-index.md` — update progress (M1-M22).
- `docker-compose.yml` — add the `ui` service.
- `pyproject.toml` — add `jinja2`, `httpx`, and the `hiveplane-ui` script.

## Future enhancements

Explicitly deferred so v0.1.0 stays inside the Python gates. The HTTP-API boundary is chosen
partly so these can land without reworking the control plane.

**UI platform**

- **React operator UI (D9 target).** Because the UI only speaks the REST API, a React app can
  replace the SSR templates screen by screen, reusing the same endpoints.
- **Real-time updates.** Polling or SSE for run/fleet state instead of full page reloads
  (D9 open question).
- **Authentication and authorization.** Today the operator is a free-text form field. Add
  identity, per-role permissions, and audit attribution (DD-07) so approve/deny/intervene are
  trustworthy in production.
- **Charts and visualizations.** Server-rendered or client-side pass-rate trends, spend bars,
  waste breakdowns, and drift markers.
- **Search, filtering, and pagination** across fleet, runs, and certifications.

**Additional screens (from D9 roadmap)**

- **Agent health view** (v0.3): readiness, failure rate, SLO status, drift indicator.
- **Triggers management UI** (v0.2): list/create/edit/delete trigger rules; event history and
  dedup stats.
- **Tools & MCP registry UI** (v0.2): tool list, trust levels, referencing workloads, trust
  change history.
- **Certification actions:** "certify now" and version compare from the dashboard.
- **Per-workload certification detail:** attestation verification, per-task benchmark
  results, drift schedule, replay trace links.

**Data and backend**

- **Operator read-model API** (Approach B): a server-computed `/operator/fleet`,
  `/operator/certifications`, `/operator/spend` to remove UI-side aggregation as the fleet
  grows.
- **Postgres-backed spend aggregation** and period filters (today currently assumes the
  in-memory attribution list, all-time).
- **Cost-per-completed-task, waste breakdown, and ROI flags** (D9 spend view expansion).
- **Model-identity mismatch warnings** surfaced prominently.
- **Trace deep-linking** to the configured OTel backend (Grafana Tempo / Jaeger).
- **Persisting populated fleet fields** (`failure_count`, `last_run_at`, `last_failure_at`) so
  the catalog can serve the fleet list without scanning runs.

## Out of scope (v0.1.0)

- React/Node toolchain and any client-side framework build.
- Triggers, tools, and agent-health management screens.
- Certification/environment mutation (register, certify, promote) from the UI.
- Authentication, multi-tenancy, and RBAC.
- Real-time streaming.

## References

- [Operator UI Design (D9)](operator-ui-design.md)
- [WBS v0.1.0 Part 11: CLI & Operator UI](../wbs/v0.1.0/wbs-v0.1.0-part11-cli-operator-ui.md)
- [PRD 02: Architecture](../prd/02-architecture.md)
- [PRD 05: Features](../prd/05-features.md)
- [Budget enforcement design](budget-enforcement-design.md)
- [Certification pipeline design](certification-pipeline-design.md)
- [Telemetry design](telemetry-design.md)
- [User guide](../USER_GUIDE.md)
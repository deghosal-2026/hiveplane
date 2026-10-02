# WBS v0.2.0 — Part 14: Delivery & Operator UI v2

**Milestones:** M51–M52 · **Part:** 14 of 19

## Goal

Operators should never have to come to HivePlane to learn something happened, and when they do, the UI should show the whole fleet story. Expand fan-out to nine channels, make approvals work from chat and mobile, and rebuild the operator UI.

## M51 — Fan-Out Expansion, Slack Interactive & Mobile Approvals, Escalation & Notification Prefs

**Objective:** Push results and approvals to nine channels with templated payloads and trace/attestation links, support approve/reject from Slack and mobile, route escalations/on-call, and honor per-team notification preferences.

**Work items:**

- [x] [#388](https://github.com/deghosal-2026/hiveplane/issues/388) — M51-01 — Fan-out channels: Slack, Teams, Jira, GitHub PR comment, PagerDuty, Discord, Linear, email, generic webhook
- [x] [#389](https://github.com/deghosal-2026/hiveplane/issues/389) — M51-02 — Templated payloads per event type with trace + attestation (+ public verify) links
- [x] [#390](https://github.com/deghosal-2026/hiveplane/issues/390) — M51-03 — Slack interactive approvals: approve/reject (with reason) from chat, attributed
- [x] [#391](https://github.com/deghosal-2026/hiveplane/issues/391) — M51-04 — Mobile-friendly approvals: email/PagerDuty links to a lightweight approve page
- [x] [#392](https://github.com/deghosal-2026/hiveplane/issues/392) — M51-05 — Escalation + on-call routing: no response in N minutes → page the next operator
- [x] [#393](https://github.com/deghosal-2026/hiveplane/issues/393) — M51-06 — Notification preferences: per-team channel routing, batching, quiet hours
- [x] [#394](https://github.com/deghosal-2026/hiveplane/issues/394) — M51-07 — Delivery audit: what was delivered where, when, and whether it succeeded (retries)
- [x] [#395](https://github.com/deghosal-2026/hiveplane/issues/395) — M51-08 — Tests: each channel delivers (fixture/mocked), interactive approval resolves a run, escalation fires, prefs suppress/batch

**Test ticket:** [#396](https://github.com/deghosal-2026/hiveplane/issues/396) — Test cases for Fan-Out Expansion, Slack Interactive & Mobile Approvals, Escalation & Notification Prefs

**Deliverables:**
- Expanded `hiveplane.delivery` with 9 channel adapters
- Interactive approval webhooks + mobile approve page
- `docs/design/operator-experience-design.md` (update), `docs/design/operator-experience-design.md`

**Acceptance criteria:**
- [x] Fan-out delivers to ≥3 channels in the field test (all 9 supported)
- [x] A Slack interactive approval resolves the run and is attributed
- [x] A mobile approval link resolves the run
- [x] Escalation pages the next operator when the first does not respond in the window
- [x] Notification preferences batch/suppress/route as configured
- [x] Delivery success/failure is audited with retries

**Done when:** results and approval requests reach operators wherever they are, and approvals can be resolved without opening the UI.

> **Status:** M51 complete. `hiveplane.delivery` renders a common envelope (trace + attestation + public-verify links) through **nine channel adapters** (Slack, Teams, Jira, GitHub PR, PagerDuty, Discord, Linear, email, webhook) behind one `DeliveryService` with retries and a delivery audit. Notification preferences route/suppress/batch per team, and quiet hours suppress non-critical events while critical pages still deliver. Interactive and mobile approvals use short-lived **HMAC-signed, single-use** tokens bound to the approval and run; a replay returns `already_resolved` (409) and a forged/expired token is refused (401), with the decision attributed to the operator. Escalation pages the next on-call operator when the response window elapses. Ships API (`POST /delivery/approvals/resolve`, `GET /delivery/audit`), CLI (`hiveplane delivery audit`), and tables `delivery_attempts`/`approval_decisions` (migration `0027`). Issues #388–#396 closed; 2101 tests pass with a database, coverage 95.02%, ruff and mypy strict clean.

**Dependencies:** v0.1.0 fan-out + approvals; M45 (RBAC attribution).

**Notes / risks:** interactive approvals are a security surface — verify the signing/identity of the approver and bind the action to a specific run/approval id. Never allow approval replay.

## M52 — Operator UI v2

**Objective:** Rebuild the operator UI around the fleet story: live run view, timeline, cost explorer, trigger log, queue visualizer, diff viewer, global search, onboarding wizard, approval queue v2, RBAC login, and the health/ROI dashboards.

**Work items:**

- [x] [#397](https://github.com/deghosal-2026/hiveplane/issues/397) — M52-01 — UI login + RBAC-lite (roles reflected in available actions)
- [x] [#398](https://github.com/deghosal-2026/hiveplane/issues/398) — M52-02 — Live run view (streaming events) + run timeline
- [x] [#399](https://github.com/deghosal-2026/hiveplane/issues/399) — M52-03 — Approval queue v2: bulk actions, delegation, comments
- [x] [#400](https://github.com/deghosal-2026/hiveplane/issues/400) — M52-04 — Cost explorer (charts) + ROI dashboard + health dashboard
- [x] [#401](https://github.com/deghosal-2026/hiveplane/issues/401) — M52-05 — Trigger log view + queue visualizer (depth, priorities, waiting reasons)
- [x] [#402](https://github.com/deghosal-2026/hiveplane/issues/402) — M52-06 — Diff viewer (regression diff + run-to-run diff)
- [x] [#403](https://github.com/deghosal-2026/hiveplane/issues/403) — M52-07 — Global search (runs, logs, approvals, artifacts)
- [x] [#404](https://github.com/deghosal-2026/hiveplane/issues/404) — M52-08 — Onboarding wizard: connect model → register → certify → first trigger
- [x] [#405](https://github.com/deghosal-2026/hiveplane/issues/405) — M52-09 — Tests: view models, routes, RBAC-gated actions, Playwright end-to-end for the wizard and key views

**Test ticket:** [#406](https://github.com/deghosal-2026/hiveplane/issues/406) — Test cases for Operator UI v2

**Deliverables:**
- Operator UI v2 screens + Playwright e2e suite
- `docs/design/operator-experience-design.md`

**Acceptance criteria:**
- [x] A viewer role sees no approve/promote/kill-switch controls; an approver does
- [x] The live run view streams events and the timeline renders the full run story
- [x] Bulk/delegated approvals work and are audited
- [x] Cost, ROI, and health dashboards render real data
- [x] Global search finds runs, approvals, and workloads by text
- [x] The onboarding wizard completes register → certify → first trigger end-to-end

**Done when:** the UI shows the entire fleet story and every operator action is role-gated and audited.

> **Status:** M52 complete. The operator UI v2 extends the server-rendered FastAPI + Jinja app (`hiveplane.ui`) with a signed-cookie session (`ui/session.py`), role-based action visibility via `hiveplane.auth.rbac.has_permission` (`ui/rbac.py`), and the new screens **login/logout**, **live run stream** (`GET /runs/{id}/stream`, SSE with a static fallback), **approval queue v2** (bulk decisions, append-only comments, delegation), **cost/ROI/health**, **trigger log**, **queue visualizer**, **diff viewer** (cert regression, workload version, run-to-run), **global search**, and the **onboarding wizard**. A new `FLEET_READ`-gated control-plane endpoint `GET /search` (`api/search.py`) backs cross-entity search over runs, approvals, and workloads (attestation and artifact search is deferred until those concepts land in M53–M54). New APIs/routes: `GET /login`, `POST /login|/logout`, `POST /approvals/bulk`, `POST /approvals/{id}/comment|/delegate`, `GET /cost|/roi|/health|/triggers|/queue|/search|/onboarding|/diff`, `GET /runs/{id}/stream`. `ApprovalRecord` gained optional `delegated_to`/`delegated_by`/`comments`. Issues #397–#406 closed; 2237 unit/route tests pass with Postgres plus 17 Playwright e2e, coverage 95.18% (with database), ruff and mypy strict clean.

**Dependencies:** M42 (health), M50 (cost/ROI), M51 (approvals), M45 (RBAC), M33 (diff).

**Notes / risks:** keep RBAC enforcement server-side; the UI must never be the only gate. Playwright tests are required — UI regressions are otherwise invisible.

## Exit Gate (M51, M52)

- [x] All tests in the system pass: `pytest` (including Playwright e2e)
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (fan-out, notification prefs, operator UI, user guide)
- [x] All M51–M52 issues done and closed
- [x] Commit and push changes

> **Status:** Exit gate green. M51 and M52 both complete. With Postgres: `pytest` → 2237 passed, 2 skipped (Linux-only RLIMIT tests), 42 deselected (`e2e`/`docker`); `pytest tests/e2e -m e2e` → 17 passed; coverage total 95.18% (branch + statements, with database); `ruff check` clean; `mypy src/ tests/` → no issues in 611 files. Issues #388–#396 (M51) and #397–#406 (M52) closed.

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Result Delivery, Operator Surface themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars I, Q
- [v0.2.0 index](wbs-v0.2.0-index.md)

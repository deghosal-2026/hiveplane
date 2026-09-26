# WBS v0.2.0 — Part 14: Delivery & Operator UI v2

**Milestones:** M51–M52 · **Part:** 14 of 19

## Goal

Operators should never have to come to HivePlane to learn something happened, and when they do, the UI should show the whole fleet story. Expand fan-out to nine channels, make approvals work from chat and mobile, and rebuild the operator UI.

## M51 — Fan-Out Expansion, Slack Interactive & Mobile Approvals, Escalation & Notification Prefs

**Objective:** Push results and approvals to nine channels with templated payloads and trace/attestation links, support approve/reject from Slack and mobile, route escalations/on-call, and honor per-team notification preferences.

**Work items:**

- [ ] [#388](https://github.com/deghosal-2026/hiveplane/issues/388) — M51-01 — Fan-out channels: Slack, Teams, Jira, GitHub PR comment, PagerDuty, Discord, Linear, email, generic webhook
- [ ] [#389](https://github.com/deghosal-2026/hiveplane/issues/389) — M51-02 — Templated payloads per event type with trace + attestation (+ public verify) links
- [ ] [#390](https://github.com/deghosal-2026/hiveplane/issues/390) — M51-03 — Slack interactive approvals: approve/reject (with reason) from chat, attributed
- [ ] [#391](https://github.com/deghosal-2026/hiveplane/issues/391) — M51-04 — Mobile-friendly approvals: email/PagerDuty links to a lightweight approve page
- [ ] [#392](https://github.com/deghosal-2026/hiveplane/issues/392) — M51-05 — Escalation + on-call routing: no response in N minutes → page the next operator
- [ ] [#393](https://github.com/deghosal-2026/hiveplane/issues/393) — M51-06 — Notification preferences: per-team channel routing, batching, quiet hours
- [ ] [#394](https://github.com/deghosal-2026/hiveplane/issues/394) — M51-07 — Delivery audit: what was delivered where, when, and whether it succeeded (retries)
- [ ] [#395](https://github.com/deghosal-2026/hiveplane/issues/395) — M51-08 — Tests: each channel delivers (fixture/mocked), interactive approval resolves a run, escalation fires, prefs suppress/batch

**Test ticket:** [#396](https://github.com/deghosal-2026/hiveplane/issues/396) — Test cases for Fan-Out Expansion, Slack Interactive & Mobile Approvals, Escalation & Notification Prefs

**Deliverables:**
- Expanded `hiveplane.delivery` with 9 channel adapters
- Interactive approval webhooks + mobile approve page
- `docs/design/result-fanout-design.md` (update), `docs/design/notification-prefs-design.md`

**Acceptance criteria:**
- [ ] Fan-out delivers to ≥3 channels in the field test (all 9 supported)
- [ ] A Slack interactive approval resolves the run and is attributed
- [ ] A mobile approval link resolves the run
- [ ] Escalation pages the next operator when the first does not respond in the window
- [ ] Notification preferences batch/suppress/route as configured
- [ ] Delivery success/failure is audited with retries

**Done when:** results and approval requests reach operators wherever they are, and approvals can be resolved without opening the UI.

**Dependencies:** v0.1.0 fan-out + approvals; M45 (RBAC attribution).

**Notes / risks:** interactive approvals are a security surface — verify the signing/identity of the approver and bind the action to a specific run/approval id. Never allow approval replay.

## M52 — Operator UI v2

**Objective:** Rebuild the operator UI around the fleet story: live run view, timeline, cost explorer, trigger log, queue visualizer, diff viewer, global search, onboarding wizard, approval queue v2, RBAC login, and the health/ROI dashboards.

**Work items:**

- [ ] [#397](https://github.com/deghosal-2026/hiveplane/issues/397) — M52-01 — UI login + RBAC-lite (roles reflected in available actions)
- [ ] [#398](https://github.com/deghosal-2026/hiveplane/issues/398) — M52-02 — Live run view (streaming events) + run timeline
- [ ] [#399](https://github.com/deghosal-2026/hiveplane/issues/399) — M52-03 — Approval queue v2: bulk actions, delegation, comments
- [ ] [#400](https://github.com/deghosal-2026/hiveplane/issues/400) — M52-04 — Cost explorer (charts) + ROI dashboard + health dashboard
- [ ] [#401](https://github.com/deghosal-2026/hiveplane/issues/401) — M52-05 — Trigger log view + queue visualizer (depth, priorities, waiting reasons)
- [ ] [#402](https://github.com/deghosal-2026/hiveplane/issues/402) — M52-06 — Diff viewer (regression diff + run-to-run diff)
- [ ] [#403](https://github.com/deghosal-2026/hiveplane/issues/403) — M52-07 — Global search (runs, logs, approvals, artifacts)
- [ ] [#404](https://github.com/deghosal-2026/hiveplane/issues/404) — M52-08 — Onboarding wizard: connect model → register → certify → first trigger
- [ ] [#405](https://github.com/deghosal-2026/hiveplane/issues/405) — M52-09 — Tests: view models, routes, RBAC-gated actions, Playwright end-to-end for the wizard and key views

**Test ticket:** [#406](https://github.com/deghosal-2026/hiveplane/issues/406) — Test cases for Operator UI v2

**Deliverables:**
- Operator UI v2 screens + Playwright e2e suite
- `docs/design/operator-ui-v2-design.md`

**Acceptance criteria:**
- [ ] A viewer role sees no approve/promote/kill-switch controls; an approver does
- [ ] The live run view streams events and the timeline renders the full run story
- [ ] Bulk/delegated approvals work and are audited
- [ ] Cost, ROI, and health dashboards render real data
- [ ] Global search finds runs/approvals/artifacts by text
- [ ] The onboarding wizard completes register → certify → first trigger end-to-end

**Done when:** the UI shows the entire fleet story and every operator action is role-gated and audited.

**Dependencies:** M42 (health), M50 (cost/ROI), M51 (approvals), M45 (RBAC), M33 (diff).

**Notes / risks:** keep RBAC enforcement server-side; the UI must never be the only gate. Playwright tests are required — UI regressions are otherwise invisible.

## Exit Gate (M51, M52)

- [ ] All tests in the system pass: `pytest` (including Playwright e2e)
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] All relevant docs updated (fan-out, notification prefs, operator UI, user guide)
- [ ] All M51–M52 issues done and closed
- [ ] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Result Delivery, Operator Surface themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars I, Q
- [v0.2.0 index](wbs-v0.2.0-index.md)

# WBS v0.2.0 — Part 2: Autonomy — Triggers & Scheduling

**Milestones:** M27–M28 · **Part:** 2 of 19

## Goal

Make the fleet run itself. Trigger rules ingest events from the outside world (webhooks, GitHub, alerts, cron) and auto-start runs, with a declarative trigger DSL, safe admission rules, and a full audit trail.

## M27 — Trigger Service Core

**Objective:** Build the trigger service: a declarative DSL, webhook ingest with HMAC verification, cron scheduling, and the dedup/cooldown/rate-limit machinery that keeps triggers from stampeding the fleet.

**Work items:**

- [x] [#170](https://github.com/deghosal-2026/hiveplane/issues/170) — M27-01 — Trigger DSL schema (event matchers, filters, task template, target workload/pipeline, admission rule) with strict validation
- [x] [#171](https://github.com/deghosal-2026/hiveplane/issues/171) — M27-02 — Webhook ingest endpoint with HMAC-SHA256 verification, replay protection (timestamp + nonce), and signature rejection
- [x] [#172](https://github.com/deghosal-2026/hiveplane/issues/172) — M27-03 — Cron/scheduled trigger engine (timezone-aware, missed-schedule policy: catch-up vs. skip)
- [x] [#173](https://github.com/deghosal-2026/hiveplane/issues/173) — M27-04 — Dedup keys (event-id based) and cooldowns (per-trigger minimum interval)
- [x] [#174](https://github.com/deghosal-2026/hiveplane/issues/174) — M27-05 — Per-trigger rate limits and global ingest backpressure
- [x] [#175](https://github.com/deghosal-2026/hiveplane/issues/175) — M27-06 — Payload → task templating (typed substitution, length limits, injection-safe)
- [x] [#176](https://github.com/deghosal-2026/hiveplane/issues/176) — M27-07 — Trigger evaluation → run submission handoff (idempotent submission with idempotency key)
- [x] [#177](https://github.com/deghosal-2026/hiveplane/issues/177) — M27-08 — Tests: signature verification, replay rejection, dedup/cooldown/rate-limit behavior, cron firing, template rendering

**Test ticket:** [x] [#178](https://github.com/deghosal-2026/hiveplane/issues/178) — Test cases for Trigger Service Core

**Deliverables:**
- `hiveplane.triggers` package (schema, engine, ingest, scheduler)
- `POST /triggers/webhook/{id}` API; trigger CRUD API
- `docs/design/trigger-service-v2-design.md` (update) + DSL reference

**Acceptance criteria:**
- [x] A webhook with an invalid/missing HMAC is rejected (401/403) and audited
- [x] A replayed webhook within the protection window is rejected
- [x] Duplicate events (same dedup key) start exactly one run
- [x] A cooldown suppresses a second trigger inside the window and records the suppression
- [x] A cron trigger fires on schedule and applies the missed-schedule policy
- [x] Templated task payloads render correctly and reject unsafe substitutions

**Done when:** external events reliably and safely start runs through the trigger service, with dedup, cooldown, and rate limits enforced.

> **Status:** M27 complete. `hiveplane.triggers` (strict DSL, HMAC ingest with
> replay protection, cron/scheduler, dedup/cooldown/rate/backpressure, templating,
> engine, memory + Postgres store), migration `0006` (`trigger_nonces`), and the
> trigger CRUD + webhook API landed. All tests pass with a database; coverage 96%
> total, ruff and mypy strict clean. Issues #170–#178 closed; changes committed
> and pushed.

**Dependencies:** M25 (trigger model); v0.1.0 run submission API.

**Notes / risks:** webhook verification and replay protection are security-critical — treat as P0. Cron timezone handling is a common bug source; test DST boundaries.

## M28 — Trigger Sources, Admission & History

**Objective:** Add the concrete trigger sources operators actually use (GitHub PR/push, Prometheus Alertmanager), 24/7 watch mode, trigger→admission rules, freeze windows, and a complete trigger history/audit trail.

**Work items:**

- [x] [#179](https://github.com/deghosal-2026/hiveplane/issues/179) — M28-01 — GitHub source: PR opened/synchronized, push, label, and issue-comment (`/hiveplane`) events with signature verification
- [x] [#180](https://github.com/deghosal-2026/hiveplane/issues/180) — M28-02 — Prometheus Alertmanager source: alert firing/resolved ingest with fingerprint dedup
- [x] [#181](https://github.com/deghosal-2026/hiveplane/issues/181) — M28-03 — Watch mode: 24/7 periodic operators (deployment verification, health checks, fleet watchdog) as a first-class trigger type
- [x] [#182](https://github.com/deghosal-2026/hiveplane/issues/182) — M28-04 — Trigger→admission rules: trusted trigger auto-admits in staging; production triggers are approval-gated by policy
- [x] [#183](https://github.com/deghosal-2026/hiveplane/issues/183) — M28-05 — Maintenance windows / freeze: triggers pause and running work drains; freeze calendar
- [x] [#184](https://github.com/deghosal-2026/hiveplane/issues/184) — M28-06 — Trigger history + audit: every evaluation, suppression, admission, and rejection recorded with reason
- [x] [#185](https://github.com/deghosal-2026/hiveplane/issues/185) — M28-07 — Dead-letter queue: failed trigger deliveries parked, inspectable, and replayable (`hiveplane triggers replay`)
- [x] [#186](https://github.com/deghosal-2026/hiveplane/issues/186) — M28-08 — CLI: `hiveplane triggers list|show|create|test|replay`
- [x] [#187](https://github.com/deghosal-2026/hiveplane/issues/187) — M28-09 — Tests: each source end-to-end, admission gating, freeze behavior, DLQ replay

**Test ticket:** [x] [#188](https://github.com/deghosal-2026/hiveplane/issues/188) — Test cases for Trigger Sources, Admission & History

**Deliverables:**
- GitHub, Alertmanager, and watch-mode source adapters
- Admission-rule engine integrated with the policy engine
- Trigger history/DLQ API + CLI; `docs/design/trigger-service-v2-design.md`

**Acceptance criteria:**
- [x] A GitHub PR event starts the intended run and a `/hiveplane` comment re-triggers it
- [x] An Alertmanager alert start is deduplicated by fingerprint
- [x] A production trigger without approval is held; a staging trigger auto-admits
- [x] During a freeze window no trigger admits a run; in-flight runs drain gracefully
- [x] A failed delivery lands in the DLQ and replays successfully with the original payload
- [x] Every trigger decision is visible in history with an attributed reason

**Done when:** triggers fire from ≥3 sources with admission rules, freeze windows, DLQ replay, and a complete audit trail.

> **Status:** M28 complete. GitHub/Alertmanager/watch sources, admission-rule
> policy integration (`require_approval`), freeze windows (migration `0007`) with
> suppression and drain, per-decision history reasons + audit, DLQ replay, and the
> `hiveplane triggers` CLI landed. All tests pass with a database; coverage 95%
> total, ruff and mypy strict clean. Issues #179–#188 closed; changes committed
> and pushed.

**Dependencies:** M27; policy engine (v0.1.0 Part 5).

**Notes / risks:** admission rules must never silently bypass certification — production admission still requires a valid certification regardless of trigger trust.

## Exit Gate (M27, M28)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total ≥ 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (trigger design, DSL reference, user guide)
- [x] All M27–M28 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Run Lifecycle & Execution theme
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillar A
- [v0.2.0 index](wbs-v0.2.0-index.md)

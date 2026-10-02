# D12: Trigger Service Design

> Status: draft
> **v0.2.0:** superseded by [Trigger Service v2 (D23)](trigger-service-v2-design.md).

## Problem

Agents only run when a human remembers to invoke them. Alerts fire, PRs merge, cron ticks — nothing happens. The platform cannot operate the agent on the team's behalf. HivePlane needs a trigger and ingress layer that accepts webhooks, alert events, GitHub PR events, and cron ticks; matches them to workload trigger rules; and starts runs automatically. It must handle dedup, idempotency, scheduled/watch modes for 24/7 operation, and certification checks before firing.

## Overview

```
 External Events                    Trigger Service                     Execution
 ┌───────────┐                     ┌──────────────────────┐            ┌──────────┐
 │ Webhook   │────────────────────▶│  Ingress             │            │          │
 │ (custom)  │                     │  ┌────────────────┐  │            │          │
 └───────────┘                     │  │ Dedup +        │  │  matched   │          │
 ┌───────────┐                     │  │ Idempotency    │  │  trigger   │          │
 │ AlertMgr  │────────────────────▶│  └───────┬────────┘  │──────────▶│ Execution│
 │ PagerDuty │                     │          │           │            │   API    │
 └───────────┘                     │  ┌───────▼────────┐  │            │          │
 ┌───────────┐                     │  │ Rule Matcher   │  │            └──────────┘
 │ GitHub PR │────────────────────▶│  │ (event→workload)│ │
 └───────────┘                     │  └───────┬────────┘  │
 ┌───────────┐                     │  ┌───────▼────────┐  │
 │ Cron      │────────────────────▶│  │ Cert Check     │  │
 └───────────┘                     │  │ (block if not  │  │
 ┌───────────┐                     │  │  certified)    │  │
 │ Scheduled │     internal        │  └───────┬────────┘  │
 │ (watch)   │◀───────────────────│          │           │
 └───────────┘                     │  ┌───────▼────────┐  │
                                   │  │ Scheduler      │  │
                                   │  │ (watch mode)   │  │
                                   │  └────────────────┘  │
                                   └──────────────────────┘
```

## Webhook Ingestion

### Custom Webhooks

Any external system can deliver a webhook to HivePlane's ingress endpoint:

```
POST   /triggers/webhook/{trigger_id}
  Headers: X-HivePlane-Signature: <hmac>
  Body: application/json (arbitrary payload)

  → 202 Accepted: { accepted: true, run_id?: "..." }
  → 409 Conflict: { error: "duplicate", previous_run_id: "..." }
```

Each webhook trigger has a shared secret for HMAC signature verification. The trigger is matched to a workload via `trigger_id` (pre-registered in the manifest).

### GitHub PR Webhooks

GitHub PR events are ingested via a dedicated endpoint that understands the GitHub webhook payload format:

```
POST   /triggers/github
  Headers: X-GitHub-Event: pull_request
           X-HitHub-Delivery: <delivery_id>
           X-HitHub-Signature-256: <sha256>
  Body: GitHub PR webhook payload
```

Supported GitHub events:

| Event | Trigger condition |
|-------|-------------------|
| `pull_request` (opened, synchronize, reopened) | PR created or updated → trigger workload (e.g., repo analysis) |
| `pull_request_review` (submitted) | Review submitted → trigger workload (e.g., review integration) |
| `push` | Branch pushed → trigger workload (e.g., deployment verification) |
| `check_suite` (completed) | CI check suite completed → trigger workload (e.g., post-CI analysis) |

The trigger rule can filter by repository, branch, file paths, or event action.

### Alert Ingestion

AlertManager and PagerDuty webhooks are ingested via dedicated endpoints that normalize the alert payload:

```
POST   /triggers/alertmanager
  Body: AlertManager webhook payload (array of alerts)

POST   /triggers/pagerduty
  Headers: X-Webhook-Id: <id>
  Body: PagerDuty webhook payload
```

Normalized alert structure:

```json
{
  "source": "alertmanager",
  "alert_id": "firing-abc123",
  "severity": "critical",
  "labels": {
    "alertname": "HighErrorRate",
    "service": "api-gateway",
    "team": "platform"
  },
  "annotations": {
    "summary": "Error rate above 5% for 5 minutes",
    "runbook_url": "https://wiki.example.com/runbooks/high-error-rate"
  },
  "starts_at": "2026-09-12T10:00:00Z",
  "status": "firing"
}
```

The trigger rule matches on `labels`, `severity`, or `status` to determine which workload to invoke (e.g., an incident triage agent).

## Cron / Scheduled Triggers

Cron triggers are defined in the workload manifest:

```yaml
triggers:
  - type: cron
    id: nightly-deployment-verification
    schedule: "0 2 * * *"  # daily at 2 AM
    workload: deployment-verification-agent
    input:
      cluster: prod-cluster-01
      services: ["api-gateway", "worker", "scheduler"]
    enabled: true
```

The trigger service runs a cron scheduler that:
1. Evaluates cron expressions on a fixed tick (every 60 seconds).
2. Fires matched triggers at the scheduled time (±60 second jitter to avoid thundering herd).
3. Records the fired trigger for audit.

## Scheduled and Watch Modes

### Watch Mode (24/7 Operator)

Some workloads operate as continuous watchers — they periodically check something and act if a condition is met:

```yaml
triggers:
  - type: watch
    id: watch-new-incidents
    schedule: "*/5 * * * *"  # every 5 minutes
    workload: incident-watcher-agent
    mode: watch
    input:
      query: "status:open AND severity:high"
      source: "incident-system"
    act_on:
      condition: "new_incidents_found"
      max_concurrent_runs: 1
```

Watch mode semantics:
- The trigger fires on schedule, but the **workload decides** whether to act based on what it observes.
- `max_concurrent_runs` prevents stacking: if a previous watch run is still in progress, the trigger is skipped (not queued).
- The watch agent can escalate (via result fan-out, D15) if it detects something requiring human attention.

### Scheduled Mode

Scheduled mode is a simple periodic trigger — fire the workload at the given interval regardless of state:

```yaml
triggers:
  - type: scheduled
    id: nightly-compliance-scan
    schedule: "0 3 * * 0"  # weekly Sunday 3 AM
    workload: compliance-scan-agent
    input:
      scope: "all-services"
```

## Trigger Rule Matching

### Rule Structure

Trigger rules are defined in the workload manifest and map events to workload invocations:

```yaml
triggers:
  - type: webhook
    id: pr-analysis-trigger
    workload: repo-analysis-agent
    match:
      source: github
      event: pull_request
      actions: [opened, synchronize]
      repo: "myorg/myrepo"
      branch_pattern: "feature/*"
    input_template: !inline |
      repo: {{ event.repository.full_name }}
      pr_number: {{ event.number }}
      sha: {{ event.pull_request.head.sha }}
    dedup:
      key: "{{ event.repository.full_name }}/{{ event.number }}/{{ event.pull_request.head.sha }}"
      window_minutes: 30
    certification_required: true
    enabled: true

  - type: webhook
    id: incident-triage-trigger
    workload: incident-triage-agent
    match:
      source: alertmanager
      severity: [critical, warning]
      labels:
        team: platform
    input_template: !inline |
      alert_id: {{ event.alert_id }}
      service: {{ event.labels.service }}
      runbook: {{ event.annotations.runbook_url }}
    dedup:
      key: "{{ event.alert_id }}"
      window_minutes: 15
    certification_required: true
    enabled: true
```

### Matching Algorithm

1. **Filter by source and type** — narrow to triggers matching the event source (github, alertmanager, pagerduty, custom).
2. **Evaluate match conditions** — check `event`, `actions`, `repo`, `branch_pattern`, `severity`, `labels` against the incoming payload.
3. **Select workload** — the first matching enabled trigger wins (triggers are ordered by specificity, not registration order).
4. **Render input** — apply the input template to the event payload to produce the task input.
5. **Dedup check** — compute the dedup key; if a run with the same key exists within the window, skip (return 409 or 202 with `duplicate: true`).
6. **Certification check** — verify the workload's certification status; block if not `certified` for production context.
7. **Submit run** — call the Execution API with the rendered input.

## Dedup and Idempotency

### Dedup

Each trigger can define a dedup key derived from the event payload. The trigger service maintains a dedup cache (TTL = `window_minutes`):

- If a key is already in the cache → the event is a duplicate. The trigger returns the previous `run_id` and does not submit a new run.
- If the key is not in the cache → the event is new. Submit the run and record the key.

Default dedup window: 15 minutes. Default dedup key for webhooks: `trigger_id + sha256(payload)`.

### Idempotency

- The trigger service stores every received event with a unique `event_id` (from the source if available, e.g., GitHub `X-GitHub-Delivery`, otherwise computed).
- Re-deliveries of the same `event_id` are detected and rejected (not re-processed).
- The trigger-to-run mapping is durable: if the trigger service restarts after submitting a run but before acknowledging, the re-delivered event finds the existing run.

### At-Least-Once Delivery

The trigger service guarantees **at-least-once** delivery to the Execution API. If the Execution API is unavailable, the trigger service retries with exponential backoff (initial: 5s, max: 300s, max retries: 10). After max retries, the event is moved to a dead-letter queue and the owning team is notified.

## Certification Check Before Trigger Fires

Before submitting a run, the trigger service checks the workload's certification status (D10):

| Certification Status | Trigger Behavior |
|---------------------|-------------------|
| `certified` | Run submitted to production context |
| `provisional` | Run submitted to staging context (if trigger allows staging) |
| `uncertified` | Run blocked; notification sent to owning team |
| `quarantined` | Run blocked; notification sent with link to regression diff |

If `certification_required: false` is set on the trigger (non-default, audited), the trigger fires regardless of certification status. This is intended only for sandbox-only or experimental workloads.

## Trigger Service API

```
POST   /triggers/webhook/{trigger_id}
  → 202 Accepted: { accepted: true, run_id }
  → 409 Conflict: { error: "duplicate", previous_run_id }

POST   /triggers/github
  → 202 Accepted | 409 Conflict

POST   /triggers/alertmanager
  → 202 Accepted | 409 Conflict

POST   /triggers/pagerduty
  → 202 Accepted | 409 Conflict

GET    /triggers
  Query: ?workload_id=...&type=...&enabled=...
  → 200: { items: [...] }

GET    /triggers/{id}
  → 200: { trigger config, last_fired, last_run_id, status }

POST   /triggers/{id}/enable
POST   /triggers/{id}/disable

GET    /triggers/events
  Query: ?since=...&source=...&deduplicated=...
  → 200: { events: [...], deduplicated_count }
```

## Data Model

```
triggers
  id              TEXT PK
  workload_id     TEXT FK
  type            TEXT  -- webhook | cron | watch | scheduled
  match           JSONB
  input_template  TEXT
  dedup_config    JSONB
  certification_required BOOLEAN
  enabled         BOOLEAN
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ

trigger_events
  id              TEXT PK  -- source event_id or computed
  trigger_id      TEXT FK
  source          TEXT  -- github | alertmanager | pagerduty | cron | custom
  payload         JSONB
  received_at     TIMESTAMPTZ
  dedup_key       TEXT
  deduplicated    BOOLEAN
  run_id          TEXT  -- nullable if blocked

trigger_runs
  trigger_id      TEXT FK
  event_id        TEXT FK
  run_id          TEXT FK
  fired_at        TIMESTAMPTZ
  status          TEXT  -- submitted | blocked_cert | blocked_dedup | failed
```

## Open Questions

- **Trigger rate limiting:** should there be per-workload and per-team trigger rate limits to prevent runaway triggers during an incident storm?
- **Trigger chaining:** can one workload's completion trigger another workload? If so, how do we prevent cycles?
- **Complex matching:** is the YAML-based match DSL sufficient, or do we need a full expression language for complex conditions?
- **Watch mode persistence:** if the trigger service restarts, does a watch-mode agent lose its place? Should watch state be persisted?
- **Multi-region triggers:** in a multi-region deployment, which trigger service instance handles a given event?

## See Also

- [PRD 02: Architecture](../prd/02-architecture.md) — trigger & ingress service in the system architecture
- [PRD 05: Features](../prd/05-features.md) — trigger and watch mode feature breakdown
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — certification check before trigger fires
- [Run Lifecycle Design](run-lifecycle-design.md) (D2) — triggered runs enter the same state machine
- [Result Fan-out Design](result-fanout-design.md) (D15) — trigger-blocked notifications
- [Registry Service Design](registry-service-design.md) (D3) — trigger rules stored in workload manifest
- [Execution Sandbox Design](execution-sandbox-design.md) (D11) — triggered runs execute in sandbox

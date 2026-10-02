# D15: Result Fan-out Service Design

> Status: partial (v0.1.0). Slack and generic-webhook transports are implemented. Teams, Jira,
> **v0.2.0:** extended by [Operator Experience (D36)](operator-experience-design.md).
> and GitHub PR comments are v0.2.0. Approval re-dispatch is implemented (#129), so
> `approval.resolved` reflects work that actually completes.

## Implementation Status (v0.1.0)

| Area | Current state | Tracked by |
|------|---------------|------------|
| Slack + generic webhook delivery | Implemented (`SlackTransport`, `WebhookTransport`) | — |
| Retry ladder / delivery records | Implemented (per `FanOutService`) | — |
| Teams / Jira / GitHub PR comment | Not implemented (v0.2.0) | — |
| Approval re-dispatch | Implemented (#129) — on approval + resume the escalated call executes through the boundary (raw-worker re-drives; LangGraph re-drives the graph) | — |
| Durable delivery records / cert + attestation links | Depend on durable stores (#126) and persistent keypair (#125) | — |

### Prerequisites for meaningful fan-out

- durable certification/attestation storage (#126) and a persistent signing keypair (#125), so
  attestation links resolve and verify;
- a working approval re-dispatch loop (#129 — implemented), so `approval.resolved` reflects
  completed work;
- real tool execution (#116), so `approval.requested` corresponds to a real tool call.

## Problem

Results stay inside the platform. Teams have to come looking — they don't learn that a run completed, failed, or escalated where they already work. Operators should not have to come to HivePlane to learn something happened. Results, approvals, drift alerts, and quarantine notifications should be pushed to the channels teams already use: Slack, Teams, Jira, GitHub PR comments, and webhooks.

HivePlane fans out on run completion, failure, and escalation, with a message format that includes a result summary, a trace link, and a certification attestation link.

## Overview

```
 Run State Changes                    Result Fan-out Service
 ┌──────────┐                        ┌──────────────────────────────┐
 │ Run      │  completed/failed/     │  ┌──────────────────────┐   │
 │ Lifecycle │  escalated             │  │ Event Router         │   │
 │ (D2)     │───────────────────────▶│  │ (match run state     │   │
 └──────────┘                        │  │  to fan-out config)  │   │
 ┌──────────┐                        │  └─────────┬────────────┘   │
 │ Cert     │  quarantined/          │            │                │
 │ Pipeline │  drift                 │  ┌─────────▼────────────┐   │
 │ (D10)    │───────────────────────▶│  │ Message Formatter    │   │
 └──────────┘                        │  │ (summary + trace +   │   │
 ┌──────────┐                        │  │  attestation link)   │   │
 │ Policy   │  approval needed       │  └─────────┬────────────┘   │
 │ Engine   │───────────────────────▶│            │                │
 │ (D4)     │                        │  ┌─────────▼────────────┐   │
 └──────────┘                        │  │ Delivery Manager     │   │
                                     │  │ (retry + guarantees) │   │
                                     │  └─────────┬────────────┘   │
                                     └────────────┼────────────────┘
                                                  │
                          ┌───────────┬───────────┼───────────┬──────────┐
                          ▼           ▼           ▼           ▼          ▼
                      ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
                      │Slack │  │Teams │  │Jira  │  │ GH PR│  │Webhook│
                      └──────┘  └──────┘  └──────┘  └──────┘  └──────┘
```

## Fan-out Triggers

The fan-out service listens for events from multiple subsystems:

| Event Source | Event | When |
|-------------|-------|------|
| Run Lifecycle (D2) | `run.completed` | Run finished successfully |
| Run Lifecycle (D2) | `run.failed` | Run failed (error, timeout, budget) |
| Run Lifecycle (D2) | `run.escalated` | Run paused for human approval |
| Run Lifecycle (D2) | `run.cancelled` | Run cancelled by operator |
| Policy Engine (D4) | `approval.requested` | A tool call or action requires approval |
| Policy Engine (D4) | `approval.resolved` | An approval was approved or denied. On approve the escalated call is re-dispatched (#129), so the run completes the work it paused on |
| WorkerContext / LLM Provider | `llm.provider_error` | A model call failed at the provider |
| WorkerContext / LLM Provider | `llm.model_mismatch` | Runtime model identity ≠ attestation identity (T11) |
| Certification (D10) | `certification.quarantined` | Agent auto-quarantined due to drift |
| Certification (D10) | `certification.blocked` | Promotion blocked due to regression |
| Trigger Service (D12) | `trigger.blocked` | Trigger blocked due to certification status |
| Cost Service (D14) | `cost.waste_detected` | Waste flag raised |
| Cost Service (D14) | `cost.budget_alert` | Budget threshold exceeded |

## Destinations

### Slack

- **Mechanism:** Slack Webhook or Slack Bot API (for threaded replies and rich formatting).
- **Message format:** Block Kit JSON with result summary, status badge, trace link, and attestation link.
- **Threading:** follow-up events for the same run post as threaded replies to the original message.
- **Channels:** configured per workload in the manifest.

### Microsoft Teams

- **Mechanism:** Teams Incoming Webhook or Power Automate connector.
- **Message format:** Adaptive Card with result summary and action links.
- **Threading:** Teams conversation threading (if supported by connector).

### Jira

- **Mechanism:** Jira REST API (create issue, add comment).
- **Use case:** escalation events create a Jira issue (type configurable); completion/failure events add a comment to the linked issue.
- **Issue fields:** summary, description (result summary), priority (from run severity), labels (workload, team), link to trace.

### GitHub PR Comment

- **Mechanism:** GitHub Issues API (POST comment on PR).
- **Use case:** when a run was triggered by a PR event (D12), the result is posted as a comment on the PR.
- **Format:** markdown with result summary, trace link, and attestation link. Collapsible details for long outputs.

### Webhook (Custom)

- **Mechanism:** POST to a configured URL with HMAC signature.
- **Payload:** structured JSON with event type, run metadata, result summary, and links.
- **Use case:** integration with internal systems, ticketing, or custom notification pipelines.

## Message Format

Every fan-out message follows a common structure, adapted to the destination's format:

### Common Message Envelope

```json
{
  "event_type": "run.completed",
  "run_id": "run-abc123",
  "workload_id": "incident-triage-agent",
  "workload_name": "Incident Triage Agent",
  "team": "platform",
  "status": "completed",
  "summary": "Triaged alert HighErrorRate for api-gateway. Severity: high. Recommended action: restart api-gateway pod-7.",
  "trace_link": "https://hiveplane.example.com/runs/run-abc123",
  "attestation_link": "https://hiveplane.example.com/certs/att-20260912-xyz789",
  "cost_usd": 0.12,
  "duration_seconds": 42,
  "timestamp": "2026-09-12T10:01:42Z",
  "trigger_source": "alertmanager",
  "trigger_event_id": "firing-abc123"
}
```

### Escalation Message

```json
{
  "event_type": "run.escalated",
  "run_id": "run-abc123",
  "workload_id": "incident-triage-agent",
  "status": "escalated",
  "summary": "Agent requests approval to restart service-b in production. Blast radius: service-level. Justification: health check failing, auto-recovery exhausted.",
  "approval_link": "https://hiveplane.example.com/approvals/appr-001",
  "trace_link": "https://hiveplane.example.com/runs/run-abc123",
  "approval_deadline": "2026-09-12T10:15:00Z",
  "policy_rule": "prod-restart-approval-required",
  "cost_usd": 0.08,
  "duration_seconds": 18
}
```

### Quarantine Notification

```json
{
  "event_type": "certification.quarantined",
  "workload_id": "incident-triage-agent",
  "workload_name": "Incident Triage Agent",
  "team": "platform",
  "summary": "Agent quarantined due to drift. Pass rate dropped from 0.92 to 0.78 (baseline: 0.92). 3 tasks regressed.",
  "regression_diff_link": "https://hiveplane.example.com/certs/compare/att-001/att-002",
  "trace_link": "https://hiveplane.example.com/runs/tr-007-replay",
  "action_required": "Review regression diff and re-certify after fix, or roll back to manifest version 4.",
  "timestamp": "2026-09-12T03:00:12Z"
}
```

### Slack Block Kit Example

```json
{
  "blocks": [
    {
      "type": "header",
      "text": { "type": "plain_text", "text": "✅ Incident Triage Agent — completed" }
    },
    {
      "type": "section",
      "text": { "type": "mrkdwn", "text": "Triaged alert *HighErrorRate* for *api-gateway*. Severity: *high*. Recommended action: restart api-gateway pod-7." }
    },
    {
      "type": "section",
      "fields": [
        { "type": "mrkdwn", "text": "*Duration:* 42s" },
        { "type": "mrkdwn", "text": "*Cost:* $0.12" },
        { "type": "mrkdwn", "text": "*Trigger:* AlertManager" },
        { "type": "mrkdwn", "text": "*Cert:* ✅ certified" }
      ]
    },
    {
      "type": "actions",
      "elements": [
        { "type": "button", "text": { "type": "plain_text", "text": "View Trace" }, "url": "https://hiveplane.example.com/runs/run-abc123" },
        { "type": "button", "text": { "type": "plain_text", "text": "View Attestation" }, "url": "https://hiveplane.example.com/certs/att-20260912-xyz789" }
      ]
    }
  ]
}
```

## Fan-out Configuration

Fan-out is configured per workload in the manifest:

```yaml
fanout:
  on_completed:
    - destination: slack
      config:
        channel: "#incident-response"
        thread_followups: true
    - destination: webhook
      config:
        url: "https://hooks.example.com/hiveplane-results"
        secret: !ref secrets/fanout-webhook-secret
  on_failed:
    - destination: slack
      config:
        channel: "#incident-response"
        thread_followups: true
    - destination: jira
      config:
        project: "INC"
        issue_type: "Bug"
        priority: "High"
        labels: ["agent-failure", "incident-triage"]
  on_escalated:
    - destination: slack
      config:
        channel: "#incident-approvals"
        mention: "@oncall"
    - destination: jira
      config:
        project: "INC"
        issue_type: "Task"
        priority: "High"
  on_certification_quarantined:
    - destination: slack
      config:
        channel: "#platform-agents"
        mention: "@platform-team"
```

### Default Fan-out

If a workload has no fan-out config, a team-level default applies (if configured). If no team default exists, no fan-out occurs (results are only visible in the UI).

## Retry and Delivery Guarantees

### Delivery Semantics

The fan-out service provides **at-least-once** delivery to each destination. Messages may be delivered more than once in the case of retries; consumers must be idempotent (dedup by `run_id` + `event_type`).

### Retry Strategy

| Attempt | Delay | Max delay |
|---------|-------|-----------|
| 1 | Immediate | — |
| 2 | 30s | — |
| 3 | 2m | — |
| 4 | 10m | — |
| 5 | 30m | — |
| 6+ | 1h | 24h |

After max retries (default: 10, configurable per destination), the message is moved to a **dead-letter store** and the owning team is notified via a fallback channel (if configured).

### Per-Destination Guarantees

| Destination | Timeout | Success signal |
|-------------|---------|----------------|
| Slack | 10s | HTTP 200 + `ok` body |
| Teams | 10s | HTTP 202 |
| Jira | 15s | HTTP 201 (create) / 201 (comment) |
| GitHub PR | 10s | HTTP 201 |
| Webhook | 30s | HTTP 2xx |

A destination that times out is retried. A destination that returns 4xx (except 429) is **not** retried (the message is malformed or unauthorized — moved to dead-letter immediately). A 429 or 5xx triggers retry.

## Approval Notifications

When the policy engine (D4) produces an `escalate` decision, the fan-out service delivers an approval notification:

- **Slack:** message to the configured approvals channel with `@oncall` mention, approval deadline, and inline action buttons (Approve / Deny) if Slack interactive messages are configured.
- **Teams:** Adaptive Card with action buttons.
- **Jira:** issue created with approval deadline as due date.
- **Webhook:** structured payload with approval link and deadline.

Approval notifications include:
- The action being requested (e.g., "restart service-b in production")
- The blast radius and policy rule that triggered escalation
- The trace link (so the approver can inspect the agent's reasoning)
- The deadline (after which the run is auto-cancelled or auto-denied per policy)

## Data Model

```
fanout_configs
  workload_id     TEXT FK
  event_type      TEXT  -- on_completed | on_failed | on_escalated | ...
  destination     TEXT  -- slack | teams | jira | github_pr | webhook
  config          JSONB
  enabled         BOOLEAN
  PRIMARY KEY (workload_id, event_type, destination)

fanout_deliveries
  id              TEXT PK
  run_id          TEXT FK
  workload_id     TEXT FK
  event_type      TEXT
  destination     TEXT
  config          JSONB
  message         JSONB  -- formatted message
  status          TEXT  -- pending | delivered | failed | dead_letter
  attempts        INTEGER
  last_attempt    TIMESTAMPTZ
  delivered_at    TIMESTAMPTZ
  error           TEXT  -- last error if failed

fanout_dead_letters
  id              TEXT PK
  delivery_id     TEXT FK
  run_id          TEXT
  destination     TEXT
  message         JSONB
  error           TEXT
  dead_lettered_at TIMESTAMPTZ
  resolved        BOOLEAN  -- manually resolved by operator
```

## Fan-out Service API

```
GET    /fanout/deliveries
  Query: ?run_id=...&workload_id=...&status=...&destination=...
  → 200: { items: [...], total }

POST   /fanout/deliveries/{id}/retry
  → manually retry a dead-lettered delivery
  → 202: { status: "pending" }

GET    /fanout/dead-letters
  → 200: { items: [...] }

POST   /fanout/dead-letters/{id}/resolve
  Body: { note }
  → 200: { resolved: true }
```

## Open Questions

- **Delivery ordering:** if multiple events fire for the same run (e.g., `escalated` then `completed`), do we guarantee delivery order? Or is best-effort sufficient?
- **Message templating:** should the message format be fully customizable per workload (Jinja templates), or are the built-in formats sufficient?
- **Rate limiting per destination:** should the fan-out service rate-limit deliveries to a Slack workspace to avoid being rate-limited by Slack's API?
- **Fan-out to multiple Slack workspaces:** in a multi-tenant scenario, how do we route to the correct workspace?
- **Sensitive data in messages:** should the fan-out service redact potential secrets from result summaries before delivering? How aggressive should the redaction be?

## See Also

- [PRD 02: Architecture](../prd/02-architecture.md) — result fan-out service in the system architecture
- [PRD 05: Features](../prd/05-features.md) — result delivery feature breakdown
- [Run Lifecycle Design](run-lifecycle-design.md) (D2) — run state changes trigger fan-out
- [Policy Engine Design](policy-engine-design.md) (D4) — escalation events trigger approval notifications
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — quarantine and drift events trigger notifications
- [Trigger Service Design](trigger-service-design.md) (D12) — trigger-blocked notifications
- [Cost Service Design](cost-service-design.md) (D14) — waste and budget alerts trigger fan-out
- [Operator UI Design](operator-ui-design.md) (D9) — fan-out delivery status visible in UI

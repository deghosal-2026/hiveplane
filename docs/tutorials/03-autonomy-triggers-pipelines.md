# Tutorial 3 — Autonomy: Triggers & Pipelines

Make the fleet run itself: start a run from an external event, then chain agents into a
pipeline with a per-step approval gate.

Prerequisites: [Tutorial 1](01-getting-started.md). `repo-agent` should be certified; a
pipeline can use any certified workloads (create a second small workload if you like).

## 1. Declare a trigger

A trigger names a `source`, a `target` (workload or pipeline), an event `filter`, an
injection-safe `task_template`, dedup/cooldown/rate controls, and an `admission_rule`
(`staging-auto`, `gated`, or `deny`). Production admission still requires certification
regardless of trigger trust.

```bash
curl -X POST localhost:8100/triggers -H 'content-type: application/json' -d '{
  "id": "pr-analysis",
  "source": "webhook",
  "target": {"kind": "workload", "ref": "repo-agent"},
  "task_template": {"pr": "{{ event.number }}"},
  "admission_rule": "staging-auto"
}'
```

`{{ event.number }}` is a value-only substitution — no expressions, no code, bounded
lengths — so an event cannot inject behavior.

## 2. Deliver a signed event

Webhook events are authenticated with HMAC-SHA256 over `timestamp.nonce.raw_body`:

```bash
BODY='{"number": 42}'
TS=$(date +%s)
NONCE=$(openssl rand -hex 8)
SIG=$(printf '%s.%s.%s' "$TS" "$NONCE" "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')

curl -X POST localhost:8100/triggers/webhook/pr-analysis \
  -H "X-HivePlane-Timestamp: $TS" \
  -H "X-HivePlane-Nonce: $NONCE" \
  -H "X-HivePlane-Signature: sha256=$SIG" \
  -d "$BODY"
```

Expected protections:

| Condition | Response |
|-----------|----------|
| Bad/missing signature | `401` |
| Replayed `(timestamp, nonce)` | `409` |
| Duplicate dedup key (within cooldown) | `409` |
| Burst past the rate bucket | `429` |

Watch the run appear:

```bash
hiveplane triggers show pr-analysis
curl -fsS localhost:8100/triggers/pr-analysis/events | jq
curl -fsS localhost:8100/triggers/pr-analysis/runs | jq
hiveplane runs list
```

If a delivery fails, it parks in the DLQ and can be re-driven:

```bash
hiveplane triggers replay <dlq-id>
```

`hiveplane triggers create|list|show|test|enable|disable` drives the service; `test` renders
a payload without submitting. `POST /triggers/github/{id}` and `/alertmanager/{id}` verify
provider signatures and normalize payloads (one alert = one event, deduped by
`fingerprint`; a `/hiveplane` PR comment re-triggers).

## 3. Freeze a workload

A freeze window suppresses matching triggers and drains in-flight runs gracefully:

```bash
curl -X POST localhost:8100/triggers/freezes -H 'content-type: application/json' -d '{
  "freeze_id": "deploy-1", "scope": "workload", "scope_ref": "repo-agent",
  "starts_at": "2026-01-01T00:00:00Z", "ends_at": "2026-01-01T00:30:00Z",
  "declared_by": "operator", "drain": "graceful"
}'
```

Suppressed events are recorded with `suppressed_freeze`, not lost. Use `drain: abort` to
stop rather than pause in-flight runs.

## 4. Chain agents into a pipeline

A pipeline is a validated DAG of workloads and control steps. Nodes hand off structured
outputs (`${node.output.field}`), fan out and reduce, pause on approval gates, and share a
cumulative budget.

```yaml
# pipeline.yaml
id: incident-response
name: Incident response
budget: { usd: 25.00, on_exceed: pause }
nodes:
  - { id: triage, kind: workload, workload: incident-triage-agent }
  - { id: remediate, kind: workload, workload: remediation-agent,
      inputs: { diagnosis: "${triage.output.diagnosis}" }, requires_approval: before }
  - { id: notify, kind: workload, workload: notify-agent,
      inputs: { summary: "${remediate.output.summary}" } }
edges:
  - { from: triage, to: remediate }
  - { from: remediate, to: notify }
```

```bash
curl -X POST localhost:8100/pipelines -H 'content-type: application/json' -d @pipeline.yaml
hiveplane pipelines list
hiveplane pipelines submit --pipeline incident-response --inputs inputs.json
hiveplane pipelines status <pipeline-run-id>
```

Node kinds: `workload`, `fan_out` (map over a list), `fan_in` (reduce with
`json_merge`/`concat`/`sum`/`first_success`), `gate` (approval only), and `transform`
(deterministic data step). `on_failure` is `fail_fast` or `continue`; `retry.max_attempts`
re-runs a failed node. Cycles are rejected before any run starts, and every handoff is
schema-checked against each workload's `spec.io`.

## 5. Resolve the mid-pipeline approval

The `remediate` node has `requires_approval: before`, so the pipeline pauses:

```bash
hiveplane approvals list --workload remediation-agent
hiveplane approvals approve <approval-id> --operator alice --reason "diagnosis looks right"
hiveplane pipelines status <pipeline-run-id>
```

Child runs carry `pipeline_origin` attribution and pass the same admission, policy, and
budget path as any run. A node budget breach pauses (or fails) per `on_exceed`.

## 6. Trigger a pipeline instead of a workload

Point a trigger's `target` at the pipeline and events flow straight into the DAG:

```json
"target": {"kind": "pipeline", "ref": "incident-response"}
```

**Next:** explore the [User Guide](../USER_GUIDE.md) for the smart task router,
agent-as-tool, GitOps reconciliation, and progressive delivery.

## See Also

- [User Guide — Trigger Service](../USER_GUIDE.md) and [Multi-Agent Pipelines](../USER_GUIDE.md)
- [Trigger service design](../design/trigger-service-design.md) · [Orchestration design](../design/orchestration-design.md)
- [Operator Runbook — DLQ and freezes](../runbooks/operator-runbook.md)

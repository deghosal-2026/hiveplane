# D24: Multi-Agent Orchestration Design

> Status: draft

**Milestones:** M29–M30 · **Extends:** D2

## Problem

The fleet runs single agents well, but real work spans agents: triage → remediate → notify. Operators chain agents by hand today — no declared DAG, no validated handoffs, no pipeline budget, no per-step approval. Two further gaps: there is no way to submit a plain-language task and let the fleet pick the right certified agent, and no safe way for one agent to call another as a tool. This design adds pipeline execution, the smart task router, and agent-as-tool composition on top of the run lifecycle (D2).

See [PRD 05: Features](../prd/05-features.md) § Multi-Agent Orchestration and [PRD 09: Roadmap](../prd/09-roadmap.md) pillar B.

## Overview

```
 submit pipeline              submit task
      │                           │
      ▼                           ▼
 ┌───────────┐             ┌──────────────┐
 │ Pipeline  │             │ Smart Task   │ cheap classifier →
 │ Spec      │             │ Router       │ best certified workload
 └─────┬─────┘             └──────┬───────┘
       │ validate DAG             │ decision + candidate scores
       ▼                          ▼
 ┌──────────────────────────────────────────┐
 │ Pipeline Engine — parent run              │
 │  node → child run (D2 lifecycle)          │
 │  handoff · gate · budget · retry          │
 └─────┬────────────────────────────────────┘
       │ child runs; agent-as-tool nested calls
       ▼
  admission (cert + budget + policy) · approvals · artifacts
```

## Design

### Pipeline Spec

A pipeline is a declarative DAG stored as desired state (canonical entity in [D21](fleet-control-data-model-design.md)). Nodes are workloads or control steps; edges express data and ordering dependencies.

```yaml
pipeline:
  id: incident-response
  budget: { usd: 25.00, on_exceed: pause }
  nodes:
    - id: triage
      kind: workload
      workload: incident-triage-agent
      inputs: { alert_payload: "${trigger.payload}" }
      on_failure: fail_fast
      retry: { max_attempts: 2, backoff_s: 30, mode: checkpoint }
    - id: fanout
      kind: fan_out
      over: "${triage.output.affected_services}"
      as: service
      node: remediate
    - id: remediate
      kind: workload
      workload: remediation-agent
      inputs: { service: "${service}", diagnosis: "${triage.output.diagnosis}" }
      requires_approval: before
      budget: { usd: 5.00 }
    - id: summary
      kind: fan_in
      from: [remediate]
      reducer: json_merge
  edges:
    - { from: triage, to: fanout }
    - { from: fanout, to: summary }
```

Node kinds: `workload` (submits a child run), `fan_out` (map over a list), `fan_in` (reduce), `gate` (approval only), `transform` (deterministic data step). Data references (`${node.output.field}`) imply data edges; `edges` add explicit ordering.

### Validation & Cycle Detection

Validation runs at registration and before every submission. It rejects unknown or non-certified workloads (for production pipelines), missing consumer inputs, duplicate node IDs, dangling edges, and cycles. Cycles are detected with Kahn's algorithm; any node left after topological sort is part of a cycle and the spec is rejected naming those nodes. Validation starts no runs.

### Execution Model

A submission creates one **parent run** (`kind: pipeline`) through the normal admission path. The engine walks the DAG and, for each ready node, submits a **child run** with `parent_run_id`, `pipeline_id`, and `node_id`. Parent state is derived from children — child runs remain the single source of truth (D2); the parent aggregates only.

| Parent state | Condition |
|--------------|-----------|
| `running` | ≥1 child active or ready |
| `paused` | gate pending, or budget exhausted with `on_exceed: pause` |
| `completed` | all required nodes completed |
| `failed` | a `fail_fast` node failed, or a required node exhausted retries |
| `cancelled` | operator cancelled the parent (cancels in-flight children) |

### Handoff Layer

Every workload declares `spec.io.input_schema` and `spec.io.output_schema`. Handoffs are schema-validated at both boundaries so mismatches never reach an agent:

1. **Producer** — a child output is validated against the producer's `output_schema` before it enters pipeline context; failure fails the node `handoff_schema_mismatch` and publishes nothing.
2. **Consumer** — mapped inputs are validated against the consumer's `input_schema` before the child is submitted; failure fails the node without submitting.

Mapping uses a restricted `${node.output.path}` language (no arbitrary code). Both validations report node, schema, and failing JSON pointer.

### Fan-Out / Fan-In

`fan_out` evaluates `over` to a list and submits N parallel child runs of the template node, binding each item to `as`. Each child is an independent run subject to admission, budget, and policy. `fan_in` waits for its `from` nodes and reduces: built-in reducers (`json_merge`, `concat`, `sum`, `first_success`) or a reducer workload. A fan-out over zero items short-circuits and publishes an empty aggregate.

### Approval Gates

`requires_approval: before|after` reuses the approval queue (D4). **Before**: the parent pauses and creates an approval request with the node's mapped inputs as evidence; approval submits the child, rejection fails the node per `on_reject`. **After**: the child runs, its output is staged, an approval request is created, and downstream nodes are blocked until approval. Gate state, approver, and decision are attributed and shown in the parent timeline.

### Pipeline Budget

The pipeline declares a cumulative cap; every child usage event rolls up to the parent, and the budget service (D5) decrements it as usage arrives. On exhaustion the parent pauses (default) or fails per `on_exceed`. A node may declare a **per-step override** capping that node's children; an override cannot exceed the remaining pipeline budget. Nested agent-as-tool calls draw from the same pipeline budget.

### Failure Semantics & Node Retry

`on_failure` is per node:

| Policy | Behavior |
|--------|----------|
| `fail_fast` | Parent fails; in-flight siblings cancelled; downstream nodes `skipped`. |
| `continue` | Node marked `failed`; independent nodes continue; dependents `skipped`. |

`retry` re-submits a failed node up to `max_attempts` with exponential backoff. Retries use a stable child identity per `(pipeline, node, attempt)` and require idempotent side effects (side-effecting tools carry idempotency keys). `mode: checkpoint` resumes from the last durable checkpoint (D18); `mode: restart` starts clean.

### Pipeline Observability

The parent timeline is derived from child runs and node records: per-node status, start/end, cost, tokens, artifacts, retry count, and approval links. Emitted as OTel spans (D8) carrying `pipeline_id`, `parent_run_id`, and `node_id`; the UI renders the DAG with per-node state and cost.

### Smart Task Router

`POST /route` takes a plain-language task and returns a target workload or pipeline. A cheap classifier model (selected through the provider seam, D17) scores the **certified catalog only**:

1. Candidates are workloads `certified` for the target context (`provisional` allowed for staging); uncertified workloads are never candidates.
2. The top candidate is chosen only if `score ≥ confidence_threshold` **and** `score − runner_up ≥ margin`.
3. Otherwise the router **refuses** with `refused: low_confidence` plus ranked candidates — it never guesses.
4. Candidate scores and the classifier model identity are recorded in the run story for explanation and audit.

A wrong route that bypasses the specialist is worse than a refusal; thresholds are conservative by default.

### Agent-as-Tool

A certified workload can be exposed as a callable tool (`tool_id: agent.<workload>`) subject to the manifest allow-list and policy. A call creates a nested child run with `invocation: agent_as_tool` and propagates:

- **Budget** — draws from the caller's/pipeline's remaining budget.
- **Policy** — evaluated at nested admission; the nested call cannot widen the caller's permissions.
- **Certification** — the nested workload must be certified for the caller's context or the call is refused.

Depth is bounded by `max_agent_depth` (default 5) and the workload-ID invocation chain is checked for cycles; either violation rejects the call. Nested cost is attributed to the calling run.

### A2A Interop (stretch)

Behind `HIVEPLANE_A2A__ENABLED`, an Agent2Agent adapter maps inbound A2A tasks to pipeline/run submissions and exposes certified workloads as A2A agents to registered remote planes. Outbound calls are limited to registered planes. A2A is explicitly stretch and must not gate M29–M30.

## Data Model

Canonical pipeline entities live in [D21](fleet-control-data-model-design.md); this design adds execution records:

| Table | Contents |
|-------|----------|
| `pipeline_runs` | parent run id, pipeline id/version, state, budget spent, timestamps |
| `pipeline_node_runs` | pipeline run id, node id, child run id, status, attempt, cost, artifacts |
| `pipeline_handoffs` | pipeline run id, from/to node, payload ref, schema-validation result |
| `router_decisions` | decision id, task hash, classifier model identity, candidates + scores, chosen, outcome |
| `agent_tool_invocations` | caller run id, nested run id, depth, workload chain, budget/policy/cert decision |

## Interfaces / API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/pipelines` | Register/update a pipeline spec (validated) |
| GET | `/pipelines/{id}` | Fetch spec and validation state |
| POST | `/pipelines/{id}/runs` | Submit a pipeline run |
| GET | `/pipeline-runs/{id}` | Parent state, node timeline, cost |
| POST | `/pipeline-runs/{id}/nodes/{node}/retry` | Retry a failed node |
| POST | `/route` | Route a plain-language task to the best certified workload |

CLI: `hiveplane pipelines submit|status`, `hiveplane route "<task>"`.

## Failure Modes

| Failure | Detection | Response |
|---------|-----------|----------|
| Cycle in spec | Kahn at validation | Reject spec, name nodes |
| Handoff schema mismatch | Producer/consumer validation | Node fails; no partial output |
| Gate rejected | Approval decision | Node fails per `on_reject`; dependents skipped |
| Budget exhausted | Parent budget counter | Pause (default) or fail per `on_exceed` |
| Node crash / lease loss | Child run state | Retry per policy; lease reassignment (D34) |
| Router low confidence | Score/margin check | Refuse with ranked candidates |
| Recursive agent-as-tool | Depth/cycle check | Reject nested call, attribute caller |

## Security

- Pipeline nodes pass the same admission as any run: certification, model-identity binding, budget, policy (DD-09, DD-15).
- Handoff payloads are shaped and injection-scanned like tool outputs (D4); a malicious node output cannot inject into the next agent.
- The router considers only certified workloads and refuses on low confidence.
- Agent-as-tool cannot escalate privilege or budget beyond the caller; depth/cycle limits bound recursion.

## Testing

Linear DAG end-to-end with validated handoffs; cycle rejection; fan-out over N items with reduced aggregate; gate pauses and resumes after approval; cumulative pipeline spend never exceeds budget; `fail_fast` vs `continue` and node retry; producer/consumer schema-mismatch rejection; router accuracy on a labeled set, uncertified never a candidate, low-confidence refusal; nested-call budget/policy/cert propagation and depth rejection.

## Open Questions

- Should a pipeline be certifiable as a unit (a pipeline corpus), or only its member workloads?
- Retry `checkpoint` semantics for frameworks without durable checkpoints.
- Reducer workloads vs. built-in reducers — when is a model-backed reduce worth the cost?
- Should the router be exposed as a certified workload (dogfooded) rather than a service?
- A2A mapping for streaming and long-running tasks.

## See Also

- [Run lifecycle design](run-lifecycle-design.md) (D2) — child run states, admission, fan-out
- [Runtime adapter v2 design](runtime-adapter-v2-design.md) (D25) — adapter capabilities for pipelines
- [Certification pipeline design](certification-pipeline-design.md) (D10) — certification gate for nodes
- [Budget enforcement design](budget-enforcement-design.md) (D5) — budget rollup and overrides
- [Policy engine design](policy-engine-design.md) (D4) — approvals and injection defense
- [Fleet execution design](fleet-execution-design.md) (D34) — leases and scheduler for child runs
- [PRD 05: Features](../prd/05-features.md) — Multi-Agent Orchestration
- [WBS v0.2.0 Part 3](../wbs/v0.2.0/wbs-v0.2.0-part3-orchestration.md) — M29–M30

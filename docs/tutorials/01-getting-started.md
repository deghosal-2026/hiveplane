# Tutorial 1 — Getting Started

Run one agent through the full HivePlane control loop: **register → certify → admit → run →
intervene → deliver**. This takes about ten minutes and uses the `repo-agent` example.

## 1. Start the control plane

The reference stack is Docker Compose: PostgreSQL, Redis, the API, the operator UI, and the
observability stack (OTel Collector, Tempo, Prometheus, Grafana).

```bash
git clone https://github.com/deghosal-2026/hiveplane.git
cd hiveplane
scripts/dev-up.sh          # copies .env.example -> .env, builds, starts, waits for health
```

Endpoints:

| Service | URL |
|---------|-----|
| Control-plane API | http://localhost:8100 |
| Operator UI | http://localhost:3001 |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Tempo | http://localhost:3200 |

Confirm the API is up:

```bash
curl -fsS http://localhost:8100/healthz && echo OK
```

The CLI defaults to `http://localhost:8100`, so no flags are needed below. (Override with
`--api-url` or `HIVEPLANE_API_URL`.)

## 2. Look at a workload manifest

A workload is declarative YAML. Open the example:

```bash
cat examples/workloads/repo-agent.yaml
```

The parts that matter for the control loop:

- `spec.runtime` — which adapter and entrypoint runs the agent.
- `spec.tools` — the MCP tools the agent may call (deny-by-default).
- `spec.corpus` — the benchmark that decides admission.
- `spec.budget` — per-run / per-day / per-team spend limits.
- `spec.approvals` — action classes that require a human.
- `spec.fan_out` — where results go.

Validate it before touching the plane:

```bash
hiveplane validate examples/workloads/repo-agent.yaml
```

## 3. Register the workload

```bash
hiveplane register examples/workloads/repo-agent.yaml
```

Registering records desired state and signs an **agent bundle** (manifest identity +
entrypoint digest). It does **not** grant production admission — that must be earned.

```bash
curl -fsS localhost:8100/workloads | jq
```

## 4. Try to run it — and be refused

Submit to production before certifying:

```bash
hiveplane submit --agent repo-agent --context production --task '{"repo": "hiveplane"}'
```

The run is refused: the workload has no valid certification. This is the thesis in action.
Production admission requires a valid, unexpired attestation for the current artifact hash
**and** a verified bundle.

## 5. Certify it

Run the benchmark corpus:

```bash
hiveplane certify repo-agent --context staging
```

This executes each corpus task, applies the staging/production thresholds, and writes an
Ed25519-signed attestation to the transparency log. Inspect it:

```bash
hiveplane certs list --workload repo-agent
hiveplane certs show <certification-id>
hiveplane verify <attestation-id>          # public, unauthenticated, exits non-zero if invalid
```

## 6. Run it for real

```bash
hiveplane submit --agent repo-agent --context production --task '{"repo": "hiveplane"}'
hiveplane runs list
hiveplane runs show <run-id>
```

Watch the run's attributed event log:

```bash
curl -fsS localhost:8100/runs/<run-id>/events | jq
```

In the operator UI (http://localhost:3001) the run detail page streams events live via
server-sent events.

## 7. Intervene

Operators stay in control:

```bash
hiveplane runs pause  <run-id>
hiveplane runs resume <run-id>
hiveplane runs stop   <run-id>
```

If the run calls a destructive tool or an action in `spec.approvals.required_for`, it pauses
and opens an approval instead:

```bash
hiveplane approvals list
hiveplane approvals approve <approval-id> --operator alice --reason "looks safe"
```

## 8. Inspect results, cost, and health

```bash
hiveplane report                    # digest over health/cost/ROI/approvals
hiveplane cost showback             # spend by tenant -> team -> workload
hiveplane health list               # readiness, SLO burn, drift, breaker state
```

Attach operator feedback — the seed of the learning loop:

```bash
hiveplane feedback <run-id> --verdict good
```

## 9. Tear down

```bash
docker compose down          # add -v to also remove volumes
```

## What you just did

You exercised the certified control loop: a workload was admitted **only** after earning a
signed certification, ran under budget/policy/sandbox, was observable and interruptible, and
its results were attributed and delivered.

**Next:** [Tutorial 2 — Certify, Promote & Survive Drift](02-certify-promote-drift.md).

## See Also

- [User Guide](../USER_GUIDE.md) · [Architecture Tour](../architecture-tour.md)
- [Operator Runbook](../runbooks/operator-runbook.md)
- [Manifest format spec](../workloads/manifest-format-spec.md)

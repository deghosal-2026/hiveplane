# Operator Runbook

Day-two operations for a running HivePlane control plane. This is the on-call companion to
the [User Guide](../USER_GUIDE.md) and the [Architecture Tour](../architecture-tour.md).

**Golden rule:** production is earned, not assumed. If you are ever unsure whether it is
safe to admit work, the safe answer is "no" — pause the fleet (incident mode) and
investigate.

---

## 1. Health check (run these first, always)

```bash
# Liveness and readiness of the API
curl -fsS http://localhost:8100/healthz
curl -fsS http://localhost:8100/readyz

# Plane metrics (Prometheus format)
curl -fsS http://localhost:8100/metrics | tail

# Fleet health and SLO burn
hiveplane health list
hiveplane health show <workload>

# Workers and the leader
hiveplane workers list
hiveplane cluster leader

# Queue depth and waiting reasons
hiveplane queue
```

Dashboard: Grafana at `http://localhost:3000` (fleet, health, cost, plane dashboards ship
in the stack). Traces: Tempo. Metrics: Prometheus.

---

## 2. Emergency procedures

### 2.1 Halt the fleet (incident mode)

Use when agents are doing something unsafe and you need it to stop **now**. Incident mode
is durable, checked directly at admission, and fails closed if unreadable.

```bash
hiveplane fleet pause --scope fleet --reason "unsafe tool calls observed" --operator alice
hiveplane fleet status            # confirm the halt is live
```

Scope it when you can: `--scope tenant:<id>` or `--scope workload:<id>`.

Resume only when resolved; the incident record is retained and attributed:

```bash
hiveplane fleet resume --operator alice
```

### 2.2 Kill a tool fleet-wide

Instant, before policy, at the tool-call boundary:

```bash
hiveplane tools disable <tool-id> --operator alice
hiveplane tools list --disabled
```

Discovery refreshes cannot resurrect a killed tool. Re-enable with `hiveplane tools enable`.

### 2.3 Quarantine a workload

Auto-quarantine is the norm (drift, repeated injection). Do it manually when a human sees
trouble first:

```bash
hiveplane drift quarantine <workload> --reason "manual hold" --operator alice
```

Quarantine revokes production admission immediately, optionally cancels in-flight runs,
notifies the owner, and is audited. **Reinstatement requires a fresh passing certification**
— there is no override:

```bash
hiveplane certify <workload> --context production
hiveplane drift reinstate <quarantine-id> --operator alice
```

### 2.4 Revoke worker / key access

```bash
hiveplane workers drain <worker-id>      # finish in-flight, take no new leases
hiveplane workers deregister <worker-id> # requeues in-flight leases
hiveplane keys revoke <key-id>           # refuses a compromised API key
```

A worker without a valid signed token is refused (401) and audited.

---

## 3. Common incidents

### 3.1 A workload drifted or was quarantined

1. `hiveplane drift quarantines --workload <id>` — read the reason and severity.
2. `hiveplane certs compare-baseline <workload> <after>` — see the exact `pass → fail`
   tasks, with latency/token/cost deltas and replayable frames.
3. Fix the cause (manifest, model, prompt, corpus expectation).
4. `hiveplane certify <workload> --context production`, then reinstate.

If the drift signal was a false positive, check `drift.required_consecutive_failures`: a
single exceeding run is only a warning by design, so repeated signals mean real decay.

### 3.2 A promotion was refused (`409`)

The reason names the changed binding (e.g.
`model_binding: openai/gpt-4o/2024-08-06 -> openai/gpt-4o/2024-11-20`). This is the gate
working. Re-certify the current artifact and promote in one step:

```bash
hiveplane promote <workload> --recertify
```

Never pin admission to an old hash to force a promotion.

### 3.3 Budget or spend-velocity paused a run

1. `hiveplane runs show <run-id>` — read the guard event (observed vs. threshold).
2. `hiveplane cost forecast` and `hiveplane cost showback` — confirm whether this is a real
   overrun or an anomaly.
3. Raise the budget in the manifest if justified (then re-certify if it affects behavior),
   or fix the workload. Runs resume cleanly; context-budget breaches never truncate
   silently.

### 3.4 Circuit breaker tripped

Breakers are per-tool and per-workload. A breach denies calls immediately with
`circuit_open`, half-opens for one bounded probe, then recovers or reopens. Check
`hiveplane health show`, verify the upstream tool, and let the breaker recover — do not
disable it to force traffic through.

### 3.5 Injection blocked / repeated attempts

`GET /security/events` lists `injection`/`egress_denied`/`taint_block`/`repeated_attempt`
events. One block is benign output hygiene; repeated attempts crossing the threshold
automatically feed quarantine. If a legitimate tool is being blocked, update the policy
pack's detector allow-list — not the scanner's version.

### 3.6 Worker died mid-run

Missed heartbeats mark the worker `unhealthy`; expired or unhealthy leases are requeued to
a healthy worker with `attempt += 1` and `reassigned_from` attribution. Confirm with
`hiveplane workers list` and `hiveplane queue`. Zombie defenses (fencing tokens) reject a
late report from the dead worker.

### 3.7 DLQ filling up

Failed trigger deliveries are dead-lettered. Inspect and replay:

```bash
hiveplane triggers replay <delivery-id>
```

Replay re-checks dedup/cooldown, issues a fresh event id, marks the delivery replayed, and
audits the action. A dead trigger with a bad signature is not replayable — fix the source.

---

## 4. Routine operations

### 4.1 Back up and restore

```bash
hiveplane backup create --out hiveplane-$(date +%F).bak
hiveplane backup verify hiveplane-$(date +%F).bak
# restore (refuses a schema mismatch or any integrity failure)
hiveplane backup restore hiveplane-$(date +%F).bak
```

The archive manifest is Ed25519-signed with per-target SHA-256 digests and the Alembic head.

### 4.2 Secrets rotation

```bash
hiveplane secrets rotate <name> --operator alice
```

The new version takes effect on the next run without redeploying workloads; in-flight pins
are unaffected; revoked versions fail closed. See
[auth-bootstrap-and-key-rotation.md](auth-bootstrap-and-key-rotation.md).

### 4.3 Retention, PII, and tenant purge

```bash
hiveplane retention enforce          # deletes only expired data, honors legal hold
```

With `HIVEPLANE_REPORTING__PII_ENABLED=true`, PII is scrubbed before the audit hash is
computed, so raw PII is never persisted. Tenant purge issues a signed `PurgeRecord`; the
global audit log is deliberately retained to preserve chain integrity, and legal hold
blocks the purge with `409`.

### 4.4 Verifying evidence

```bash
hiveplane verify <attestation-id>     # public, unauthenticated, non-zero on invalid
hiveplane audit export --period-start <t0> --period-end <t1> --format json
```

The audit export carries a Merkle-root-plus-chain-head proof; `verify` round-trips an
authentic export and rejects a tampered one. Attestations remain verifiable by id even
without credentials.

---

## 5. Chaos drills (game day)

Run drills in a sandbox/tenant before production. Production drills require
`allow_production` **and** an admin authorizer, otherwise they are refused.

```bash
hiveplane chaos drills
hiveplane chaos run kill-worker --tenant sandbox
hiveplane chaos run revoke-cert --tenant sandbox
```

Each drill emits a pass/fail report (injected fault, observed response, timing, verdict).
Recover or halt correctly is the pass condition — a drill that leaves the system wedged is
a finding.

---

## 6. Escalation checklist

When an incident is not on this page:

1. **Stop new work** — `hiveplane fleet pause` (scoped if possible).
2. **Capture evidence** — `hiveplane runs show`, `hiveplane audit export`, Grafana/Tempo.
3. **Check the immune system** — did certification/quarantine/health already flag it?
4. **Check defense** — `GET /security/events`.
5. **Preserve state** — do not delete runs or artifacts before a backup.
6. **Page the owner** — approvals and drift notify via the configured fan-out channels.

## See Also

- [User Guide](../USER_GUIDE.md) · [Architecture Tour](../architecture-tour.md)
- [k3d reference deployment](k3d-reference-deploy.md) · [Auth bootstrap & key rotation](auth-bootstrap-and-key-rotation.md)
- [Observability](../observability.md) · [Security baseline](../prd/06-security-baseline.md)

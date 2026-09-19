# D11: Execution Sandbox Design

> Status: draft. The design targets container/cgroup isolation. **v0.1.0 ships process-level
> enforcement only, and it is not yet wired into the adapter path** — the runtime adapter runs
> the agent entrypoint on an in-process daemon thread, and `InMemorySandboxManager` records
> bookkeeping and a `sandbox_id` without applying caps. Tracked by #110.

## Implementation Status (v0.1.0)

| Capability | Current state | Tracked by |
|------------|---------------|------------|
| Sandbox manager bookkeeping (provision/destroy/status/reap) | Implemented (`InMemorySandboxManager`) | — |
| Process-level caps (`RLIMIT_AS`, `RLIMIT_CPU`, wall-clock watchdog, ephemeral workdir) | Code exists (`sandbox/manager.py`) but is **not in the adapter path** | #110 |
| Adapter runs inside sandbox | **No** — runs in-process on a daemon thread | #110 |
| Container/cgroup isolation, network namespaces | Deferred beyond v0.1.0 | — |
| Egress enforcement | Tool-call-boundary string check only (`EgressGuard`); no network namespace | #110 |
| Sandbox survival across restart / interaction with durable resume | Unspecified | #111 |

The container-oriented sections below are the target design. Read them with the caveat above.

## Problem

Production agent fleets run destructive and production-affecting actions in the same context as read-only work. There is no isolation boundary, no resource caps, and no network egress control. A misbehaving agent can exhaust memory, run indefinitely, exfiltrate data, or side-effect production systems with no guardrail (DD-14).

HivePlane isolates destructive or production-affecting runs in an execution sandbox with per-run resource caps, restricted network egress, no shared filesystem with the control plane, and a lifecycle that guarantees cleanup.

## Overview

```
 ┌─────────────────────────────────────────────────────────┐
 │                   Control Plane                          │
 │                                                         │
 │   Execution API ──> Policy Engine ──> Sandbox Manager   │
 │                                         │               │
 └─────────────────────────────────────────┼───────────────┘
                                           │ create sandbox
                                           ▼
 ┌─────────────────────────────────────────────────────────┐
 │                    Sandbox                               │
 │                                                         │
 │  ┌───────────┐  ┌───────────┐  ┌───────────────────┐   │
 │  │ Resource  │  │ Network   │  │ Filesystem        │   │
 │  │ Caps      │  │ Egress    │  │ (ephemeral,       │   │
 │  │           │  │ Allowlist │  │  no shared state) │   │
 │  └───────────┘  └───────────┘  └───────────────────┘   │
 │                                                         │
 │  ┌───────────────────────────────────────────────┐     │
 │  │  Runtime Adapter (runs inside sandbox)         │     │
 │  │  ┌─────────────────────────────────────┐      │     │
 │  │  │  Agent Runtime                       │      │     │
 │  │  │  (tool calls routed through policy)  │      │     │
 │  │  └─────────────────────────────────────┘      │     │
 │  └───────────────────────────────────────────────┘     │
 │                                                         │
 │  ┌───────────────────────────────────────────────┐     │
 │  │  Tool-Output Shaping Layer                    │     │
 │  │  (filter, truncate, budget)                   │     │
 │  └───────────────────────────────────────────────┘     │
 └─────────────────────────────────────────────────────────┘
```

## Sandbox Lifecycle

### States

```
 create ──> provisioning ──> ready ──> running ──> completed ──> destroyed
                                     │     │
                                     │     └──> failed ──> destroyed
                                     └──> cancelled ──> destroyed
```

| State | Description |
|-------|-------------|
| `provisioning` | Sandbox container/process is being created; resource caps and network rules applied |
| `ready` | Sandbox is provisioned, awaiting task execution |
| `running` | Task is executing inside the sandbox |
| `completed` | Task finished successfully; results collected |
| `failed` | Task failed (error, timeout, resource exhaustion, policy violation) |
| `cancelled` | Operator or system cancelled the run |
| `destroyed` | Sandbox is torn down; all ephemeral state is gone |

### Lifecycle Guarantee

Every sandbox that enters `provisioning` **must** reach `destroyed`. A reaper process runs on a fixed interval (default: 60 seconds) and force-destroys any sandbox that has exceeded its wall-clock cap or is in a stale state. No sandbox persists indefinitely.

## Per-Run Resource Caps

Each sandbox run is bounded by resource limits defined in the workload manifest and enforced at the sandbox boundary:

```yaml
sandbox:
  resources:
    memory_mb: 512
    cpu_cores: 1.0
    wall_clock_seconds: 300
    max_output_size_mb: 10
    max_filesystem_mb: 100
    max_processes: 10
```

| Cap | Enforcement | On Exceed |
|-----|-------------|-----------|
| `memory_mb` | cgroup / container memory limit | OOM kill → run `failed` |
| `cpu_cores` | cgroup / container CPU quota | Throttle (no kill) |
| `wall_clock_seconds` | Timeout watchdog | Kill → run `failed` (reason: `timeout`) |
| `max_output_size_mb` | Output stream byte counter | Truncate → run `failed` (reason: `output_budget_exceeded`) |
| `max_filesystem_mb` | Quota on ephemeral FS | Write fails → run `failed` (reason: `fs_quota_exceeded`) |
| `max_processes` | PID namespace limit | Fork/exec fails |

Resource caps are **per-run**, not per-workload. Two runs of the same workload get independent sandboxes with independent caps.

## Restricted Network Egress

### Egress Allowlist

Network access is **deny-by-default**. The workload manifest specifies an egress allowlist:

```yaml
sandbox:
  network:
    egress: deny-by-default
    allow:
      - host: "api.openai.com"
        port: 443
        protocol: tls
      - host: "internal-metrics.corp.local"
        port: 443
        protocol: tls
      - host: "staging-db.corp.local"
        port: 5432
        protocol: tcp
        reason: "read-only replica for triage queries"
```

### Enforcement

- **Docker Compose (local dev):** network namespace + iptables rules per container
- **Container runtime (production):** network policies (e.g., Calico, Cilium) or sidecar proxy (Envoy) with L4/L7 filtering
- **Benchmark runs:** network is fully disabled unless `allow_network: true` on the specific corpus task (see D10)

### DNS Resolution

DNS is allowed for resolving allowlisted hostnames but is otherwise restricted. A DNS allowlist cache prevents DNS-based exfiltration (e.g., encoding data in subdomain queries).

## Filesystem Isolation

- The sandbox has an **ephemeral filesystem** — no shared mount with the control plane.
- Input data is copied **in** at provisioning time (fixtures, task inputs, secrets via mounted env vars).
- Output data is copied **out** at completion time (results, logs) through a controlled channel.
- No bidirectional filesystem access during execution. If the agent needs to read from a shared store, it goes through a tool call that is policy-gated (D4) and output-shaped (D13).
- Secrets are mounted as environment variables or tmpfs files, never persisted to disk inside the sandbox.

## Integration with Adapters

The runtime adapter (D6) runs **inside** the sandbox. The adapter is responsible for:

1. Receiving the task from the control plane (via a secure channel — Unix socket, vsock, or localhost HTTP).
2. Executing the agent runtime (LangGraph, raw worker, etc.).
3. Routing all tool calls through the policy boundary (the adapter does not bypass policy, even in the sandbox).
4. Reporting state transitions, usage, and tool calls back to the control plane.
5. Applying the tool-output shaping layer (filter, truncate, budget) before tool outputs reach the agent context window (DD-13).

### Adapter-to-Control-Plane Channel

```
Control Plane                          Sandbox
┌──────────┐    task + manifest        ┌──────────────────┐
│          │ ────────────────────────▶ │  Adapter         │
│ Sandbox  │                           │  ┌────────────┐  │
│ Manager  │ ◀──────────────────────── │  │ Agent      │  │
│          │  state + usage + tools    │  │ Runtime    │  │
│          │                           │  └────────────┘  │
│          │ ◀──────────────────────── │  Shaping Layer   │
│          │  shaped tool output       │                  │
└──────────┘                           └──────────────────┘
```

The channel is:
- **Authenticated:** the sandbox presents a per-run token; the control plane verifies it.
- **Scoped:** the token is valid only for this run; the sandbox cannot access other runs' state.
- **One-way egress:** the sandbox can call the control plane (for tool execution, policy checks) but the control plane does not expose its full API to the sandbox.

## Sandbox Manager API

```
POST   /sandboxes
  Body: { run_id, workload_id, manifest_version, resource_caps, network_allowlist, task_input }
  → 201: { sandbox_id, status: "provisioning" }

GET    /sandboxes/{id}
  → 200: { sandbox_id, run_id, status, resources, started_at, finished_at? }

POST   /sandboxes/{id}/cancel
  → 202: { status: "cancelled" }

DELETE /sandboxes/{id}
  → force-destroy (reaper or manual)
  → 204
```

## Benchmark vs. Production Sandboxes

| Property | Benchmark Sandbox | Production Sandbox |
|----------|-------------------|-------------------|
| Model | Pinned to exact ID | Per manifest model strategy |
| Inputs | Corpus fixtures | Live task input |
| Network | Disabled (unless `allow_network`) | Allowlist per manifest |
| Time | Fixed timestamps injected | Real clock |
| Lifetime | Single benchmark task | Single run |
| Policy | Benchmark policy pack (mirrors prod) | Production policy pack |

## Security Considerations

- **Secrets redaction:** secrets are never written to disk inside the sandbox; they are injected as tmpfs or env vars and are redacted from logs, traces, and audit events (DD-07).
- **Sandbox escape detection:** the reaper monitors for unexpected processes, network connections, or filesystem writes outside the ephemeral FS. Anomalies trigger immediate sandbox destruction and run failure.
- **Image integrity:** sandbox images are pinned by digest, not tag. A tag can be repointed; a digest cannot.
- **Capability stripping:** sandboxes run with minimal Linux capabilities (no `CAP_SYS_ADMIN`, no `CAP_NET_ADMIN`, no `CAP_NET_RAW`).

## Open Questions

- **Sandbox backend abstraction:** should we abstract over Docker, Firecracker, gVisor, or cloud sandbox services? What is the minimum backend interface?
- **GPU access:** some workloads need GPU (local model inference). How do we cap and isolate GPU resources?
- **Sandbox pooling:** for low-latency trigger-driven runs, can we pre-warm sandboxes? What are the security implications?
- **Persistent sandbox for watch mode:** watch-mode agents (D12) run continuously. Do they get a long-lived sandbox, or a sequence of short-lived sandboxes?
- **Cross-platform:** Docker Compose is the local dev backend. What about macOS without Docker Desktop?

## See Also

- [PRD 02: Architecture](../prd/02-architecture.md) — execution sandbox in the system architecture
- [PRD 05: Features](../prd/05-features.md) — safe execution feature breakdown
- [Runtime Adapter Design](runtime-adapter-design.md) (D6) — adapter runs inside the sandbox
- [Policy Engine Design](policy-engine-design.md) (D4) — tool permissions enforced at the boundary
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — benchmark runs in the sandbox
- [MCP Tool Registry Design](mcp-tool-registry-design.md) (D13) — tools called through the policy boundary
- [Design Decisions](design-decisions.md) — DD-13, DD-14

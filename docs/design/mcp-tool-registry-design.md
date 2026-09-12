# D13: MCP Tool Registry Design

> Status: draft

## Problem

Agent tools are registered ad hoc, referenced by name, and have no trust classification. There is no stable identity, no access policy, and no health tracking. A tool can be swapped, renamed, or go offline with no visibility. Workloads reference tools by string name — a rename breaks every workload that uses it.

HivePlane onboards tools via the MCP (Model Context Protocol) layer (built on mcp-fabric), assigns each tool a stable ID and trust level, and enforces access through the policy engine. Workloads reference tools by ID; policy decides who may call them and under what conditions (DD-03).

## Overview

```
 ┌──────────────────────────────────────────────────────────┐
 │                   MCP Tool Registry                       │
 │                                                          │
 │  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
 │  │ Tool Catalog │  │ Trust Levels │  │ Health Monitor│  │
 │  │ (stable IDs) │  │ (read/destr.)│  │ (availability)│  │
 │  └──────────────┘  └──────────────┘  └───────────────┘  │
 │          │                  │                 │          │
 │          └──────────┬───────┴─────────────────┘          │
 │                     ▼                                    │
 │            ┌─────────────────┐                           │
 │            │  MCP Layer       │  (built on mcp-fabric)    │
 │            │  ┌───────────┐  │                           │
 │            │  │ Tool      │  │                           │
 │            │  │ Adapters  │  │                           │
 │            │  └───────────┘  │                           │
 │            └─────────────────┘                           │
 └──────────────────────────────────────────────────────────┘
                      │
                      ▼
 ┌──────────────────────────────────────────────────────────┐
 │  Policy Engine (D4) — decides allow/deny/escalate        │
 │  per tool call, per run context                           │
 └──────────────────────────────────────────────────────────┘
                      │
                      ▼
 ┌──────────────────────────────────────────────────────────┐
 │  Execution Sandbox (D11) — tool executes in isolation    │
 │  with output shaping at the boundary                     │
 └──────────────────────────────────────────────────────────┘
```

## Tool Onboarding via MCP Layer

### MCP Protocol

Tools are onboarded through the Model Context Protocol. Each tool is an MCP server (or a wrapper around an existing tool that exposes an MCP-compatible interface). The registry uses mcp-fabric as the MCP client layer to discover, invoke, and manage tools.

### Registration Flow

1. **Tool author defines** the tool as an MCP server with a tool manifest (name, description, input schema, output schema, side-effect classification).
2. **Operator registers** the tool with HivePlane:

```
POST   /tools
  Body:
    {
      "name": "query-postgres",
      "description": "Execute a read-only SQL query against a specified database",
      "mcp_endpoint": {
        "type": "stdio",          // or "http", "sse"
        "command": "python",
        "args": ["-m", "hiveplane.tools.pg_query"],
        "env": {
          "DB_CONNECTION_FILE": "/run/secrets/pg-readonly"
        }
      },
      "input_schema": { ... JSON schema ... },
      "output_schema": { ... JSON schema ... },
      "trust_level": "read-only",
      "side_effects": false,
      "network_required": true,
      "tags": ["database", "postgres", "query"]
    }
  → 201: { tool_id: "tool-01HW...", registered: true }
```

3. **Registry assigns a stable tool ID** (ULID-based, immutable, never reused).
4. **Registry probes the tool** — invokes a health check to verify the MCP endpoint is reachable and the input/output schemas are valid.
5. **Tool is available** for workloads to reference by ID.

### Stable Tool IDs

Tool IDs are:
- **Stable:** the ID never changes for the lifetime of the tool. Renaming the tool does not change its ID.
- **Immutable:** a tool ID is never reused. If a tool is deleted, its ID is retired.
- **Versioned:** a tool can have multiple versions (e.g., schema changes). Each version shares the same tool ID but has a distinct version number. Workloads reference `tool_id` + `version` (or `latest` for the newest compatible version).

```
tool_id:   tool-01HW...        (stable, immutable)
versions:  [1, 2, 3]           (append-only)
current:   3
```

## Trust Levels

Every tool is classified with a trust level that determines how the policy engine evaluates calls to it:

| Trust Level | Description | Policy Default | Sandbox Required |
|-------------|-------------|----------------|-------------------|
| `read-only` | No side effects; reads data from a source | `allow` (if workload is authorized) | No (but recommended) |
| `destructive` | Has side effects on external systems (write, delete, restart, deploy) | `escalate` (requires approval) or `deny` | Yes (always) |
| `elevated` | Can affect production infrastructure (prod DB, prod cluster, billing) | `deny` unless explicitly allowed + approval | Yes (always) + restricted network |

### Side-Effect Declaration

Tool authors declare side effects at registration time:

```json
{
  "side_effects": true,
  "side_effect_types": ["write", "restart"],
  "reversible": false,
  "blast_radius": "service-level",
  "affected_resources": ["service:api-gateway"]
}
```

The policy engine (D4) uses this declaration, combined with the run context (staging vs. production, data sensitivity), to compute a blast-radius score and decide `allow`, `deny`, or `escalate`.

### Trust Level Enforcement

Trust levels are enforced at the **request boundary**, not inside the agent. When the runtime adapter routes a tool call:

1. The adapter resolves the tool by ID via the registry.
2. The adapter sends the tool call to the policy engine with: tool ID, tool trust level, run context, and input.
3. The policy engine returns `allow`, `deny`, or `escalate` with a reason.
4. If `allow`, the tool call is executed inside the sandbox (if destructive) with output shaping applied.
5. If `deny`, the tool call is blocked and the agent receives a denial message.
6. If `escalate`, the run pauses and an approval request is created (surfaced in the UI and via fan-out, D15).

## Tool Registration API

```
POST   /tools
  → 201: { tool_id, version: 1 }

GET    /tools
  Query: ?trust_level=...&tag=...&healthy=...
  → 200: { items: [...] }

GET    /tools/{tool_id}
  → 200: { tool_id, name, description, versions, current_version, trust_level, health }

GET    /tools/{tool_id}/versions/{version}
  → 200: { version, input_schema, output_schema, mcp_endpoint, registered_at }

POST   /tools/{tool_id}/versions
  Body: { input_schema, output_schema, mcp_endpoint, ... }
  → 201: { version: N+1 }

POST   /tools/{tool_id}/health-check
  → 200: { healthy: true, latency_ms: 120 }

DELETE /tools/{tool_id}
  → retires the tool ID (no new workloads can reference it; existing references continue to work until the workload manifest is updated)
  → 204
```

## Workloads Reference Tools by ID

In the workload manifest, tools are referenced by stable ID, not name:

```yaml
spec:
  tools:
    - tool_id: tool-01HW...        # query-postgres
      version: latest              # or a pinned version
      alias: db_query              # local alias for this workload
      config:
        connection: "staging-readonly"
    - tool_id: tool-01HX...        # restart-service
      version: 3
      alias: restart
      config:
        cluster: "staging-cluster-01"
```

The alias is local to the workload — it is the name the agent sees. The tool ID is what the registry and policy engine use. This means:
- A tool can be renamed globally without breaking any workload.
- A workload can use the same tool under different aliases (e.g., `db_query_readonly` and `db_query_admin` with different configs).
- The policy engine always sees the real tool ID and trust level, regardless of the alias.

## Tool Health and Availability

### Health Monitoring

The registry monitors tool health on a fixed interval (default: 60 seconds):

| Check | Description |
|-------|-------------|
| **Reachability** | Can the MCP endpoint be contacted? |
| **Schema validity** | Does the tool respond to a `list_tools` MCP call with a valid schema? |
| **Latency** | What is the round-trip time for a health-check probe? |
| **Error rate** | What is the recent error rate for actual tool calls (from telemetry)? |

### Health Status

| Status | Meaning | Behavior |
|--------|---------|----------|
| `healthy` | All checks pass | Tool available |
| `degraded` | Latency above threshold OR error rate > 5% | Tool available but flagged; workloads warned |
| `unhealthy` | Endpoint unreachable OR error rate > 20% | Tool unavailable; calls blocked with `tool_unhealthy` reason |
| `unknown` | No health data yet (newly registered) | Tool available but flagged |

### Health Integration

- Tool health is surfaced in the Operator UI (D9) — the fleet dashboard shows tool health alongside agent health.
- Unhealthy tools trigger a notification to the tool owner (the operator who registered the tool).
- If a workload's required tools are unhealthy, the trigger service (D12) can block triggered runs with a `tool_unavailable` reason.

## MCP Layer (mcp-fabric Integration)

The MCP layer is the transport between HivePlane and actual tool implementations:

```
HivePlane Control Plane
  │
  ├── MCP Client (mcp-fabric)
  │     │
  │     ├── stdio transport ──> Tool MCP Server (local process)
  │     ├── HTTP transport  ──> Tool MCP Server (remote HTTP)
  │     └── SSE transport   ──> Tool MCP Server (SSE endpoint)
  │
  └── Policy Engine intercepts every call
```

mcp-fabric provides:
- **Transport abstraction:** tools can be local processes (stdio), HTTP services, or SSE endpoints — the registry normalizes access.
- **Schema discovery:** automatic `list_tools` and `get_tool_schema` calls.
- **Call routing:** `call_tool(tool_id, input)` → MCP `tools/call` message.
- **Error normalization:** MCP errors are mapped to HivePlane error codes.

HivePlane adds:
- **Policy interception:** every `call_tool` is routed through the policy engine before reaching the MCP layer.
- **Output shaping:** tool outputs are filtered, truncated, and budgeted before reaching the agent context window (DD-13).
- **Telemetry:** every tool call is traced (D8) with tool ID, trust level, input summary, output size, latency, and policy decision.

## Data Model

```
tools
  id              TEXT PK
  name            TEXT
  description     TEXT
  trust_level     TEXT  -- read-only | destructive | elevated
  side_effects    BOOLEAN
  side_effect_types TEXT[]
  blast_radius    TEXT
  current_version INTEGER
  owner           TEXT
  registered_at   TIMESTAMPTZ
  retired_at      TIMESTAMPTZ  -- nullable

tool_versions
  tool_id         TEXT FK
  version         INTEGER
  input_schema    JSONB
  output_schema   JSONB
  mcp_endpoint    JSONB
  network_required BOOLEAN
  registered_at   TIMESTAMPTZ
  PRIMARY KEY (tool_id, version)

tool_health
  tool_id         TEXT FK
  version         INTEGER
  status          TEXT  -- healthy | degraded | unhealthy | unknown
  latency_ms      INTEGER
  error_rate      REAL
  last_checked    TIMESTAMPTZ
  last_healthy    TIMESTAMPTZ
```

## Open Questions

- **Tool versioning strategy:** when a tool's input schema changes in a breaking way, should the registry auto-detect this and require a new major version? How do we handle backward compatibility?
- **Tool sandboxing:** should tool MCP servers run inside the execution sandbox (D11) alongside the adapter, or outside? Destructive tools probably should, but read-only tools might not need to.
- **Tool provenance:** should the registry track who built a tool, its source repository, and a signed build artifact hash?
- **Dynamic tool discovery:** should agents be able to discover available tools at runtime (MCP `list_tools`), or should the manifest strictly enumerate the allowed tool set?
- **Tool composition:** can one tool call another tool (tool chaining)? If so, how does policy apply to the chained call?

## See Also

- [PRD 02: Architecture](../prd/02-architecture.md) — MCP tool registry in the system architecture
- [PRD 05: Features](../prd/05-features.md) — tools & MCP feature breakdown
- [Policy Engine Design](policy-engine-design.md) (D4) — tool permissions and trust level enforcement
- [Execution Sandbox Design](execution-sandbox-design.md) (D11) — destructive tools execute in sandbox
- [Runtime Adapter Design](runtime-adapter-design.md) (D6) — adapter routes tool calls through policy
- [Workload Manifest Design](workload-manifest-design.md) (D1) — tools referenced by ID in manifest
- [Design Decisions](design-decisions.md) — DD-03, DD-13

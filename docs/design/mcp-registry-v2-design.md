# D32: MCP Registry v2 Design

> Status: implemented (M44)

**Milestones:** M44 · **Extends:** D13

## Problem

D13 defined the MCP tool registry model — stable IDs, trust levels, manifest references — but shipped no live transport: tools were fixture-backed, and the registry could not connect to real MCP servers or discover their tools. Workloads could not call real tools through the boundary, dynamic discovery had no trusted stable identity, and third-party output had no enforced path through shaping and injection scanning.

D32 makes the registry live: connect real MCP servers via mcp-fabric, discover and refresh tools, onboard with `hiveplane tools`, enforce trust levels and manifest allow-lists at the request boundary, and route every call through policy, shaping, and injection scanning. D32 is authoritative for v0.2.0 MCP behavior.

## Overview

```
 hiveplane tools add ─▶ MCP Registry v2 (catalog · discovery · stable IDs)
                              │ tools/call (mcp-fabric)
                              ▼
   allow-list ─▶ policy(D29) ─▶ kill switch ─▶ transport ─▶ real MCP servers
                              │                          (stdio|http|sse)
                              ▼
   agent context ◀── taint ◀── shaping + injection scan ◀── result
```

## Design

### Live MCP Transport

The registry uses mcp-fabric as its MCP client layer to connect real servers over `stdio`, `http`, or `sse`, and normalizes:

- **`list_tools`** — enumerate tools with name, description, and input/output schema.
- **`tools/call`** — invoke a tool and return its real result (never fabricated).
- **Error normalization** — MCP errors map to HivePlane codes (`tool_unreachable`, `tool_error`, `tool_timeout`, `schema_mismatch`).
- **Timeouts and cancellation** — bounded per call; cancellation propagates from the run.

Connections are pooled per server; a server restart does not change tool identity.

### Dynamic Discovery & Refresh

Discovery refreshes the catalog on a schedule and on demand. New tools appear as `discovered` (not callable) until onboarded or explicitly auto-admitted by policy. Removed tools are marked `absent`; workloads referencing an absent tool fail admission with a reason rather than silently calling a different tool. Discovery never invents IDs: identity is derived from the server fingerprint plus the tool name.

### Onboarding & Stable Tool IDs

```
hiveplane tools add --server stdio://python -m my.tools --trust read-only
hiveplane tools list [--server ...] [--trust ...] [--healthy]
hiveplane tools show tool-01HW...
hiveplane tools remove tool-01HW...
```

Identity is **stable across server restarts**: `tool_id` is a ULID assigned once at onboarding and stored with `{server_fingerprint, tool_name}`. Reconnection matches on that pair and reuses the ID. A tool renamed on the server becomes a new ID; the old ID is retired, never reused. Versions are append-only (D13).

### Trust Levels at the Request Boundary

Trust is assigned at onboarding and enforced at the boundary, not inside the agent:

| Trust | Policy default | Sandbox |
|-------|----------------|---------|
| `read-only` | allow if allow-listed | optional |
| `destructive` | escalate/deny; approval per policy | required |
| `elevated` | deny unless explicitly allowed + approval | required + restricted egress |

The boundary resolves the real `tool_id` and trust level even when the workload uses a local alias.

### Manifest Allow-Lists

A workload may only call tools in its manifest allow-list:

```yaml
spec:
  tools:
    - { tool_id: tool-01HW..., version: latest, alias: db_query }
    - { tool_id: tool-01HX..., version: 3, alias: restart }
```

A call outside the list is denied with `tool_not_allowed`. The allow-list is evaluated before policy, so even a permissive pack cannot widen a workload's tool surface.

### Execution Through the Boundary

Every call flows: allow-list → policy (D29) → kill switch → transport → result → shaping → injection scan → taint tag → agent context. Usage (tokens/tool calls/cost) and the policy decision are recorded (D5, D8, DD-07). Results are real; the agent never receives a fabricated tool result.

### Untrusted Output, Policy & Kill Switch

Third-party output is `untrusted` by default (D29): it is shaped (filter, truncate, budget — DD-13) and injection-scanned before reaching context, and taint propagates so an untrusted value cannot silently reach a destructive tool. Tool trust and IDs feed context-aware policy; the kill switch (D29) is checked at the boundary before the transport, so a killed tool is denied near-instantly even if policy would allow it, and discovery refreshes cannot resurrect a killed tool.

## Data Model

```
mcp_servers
  id PK, endpoint JSONB, fingerprint, status, last_seen

tools
  id PK, server_id FK, tool_name, trust_level,
  status,            -- discovered | active | absent | retired
  current_version, discovered_at, onboarded_at

tool_versions
  tool_id FK, version, input_schema JSONB, output_schema JSONB, registered_at
  PRIMARY KEY (tool_id, version)
```

## Interfaces / API

```
POST   /mcp/servers                 # register/connect a server
GET    /mcp/servers
POST   /mcp/servers/{id}/refresh    # dynamic discovery
GET    /tools?status=active
POST   /tools/{id}/onboard
DELETE /tools/{id}
```

CLI: `hiveplane tools add|list|show|remove`, `hiveplane mcp servers`.

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Server restart | reconnect matches `fingerprint + tool_name`, reuses `tool_id` |
| Tool absent after discovery | marked `absent`; new runs fail admission with a reason |
| Schema drift on a tool | new version created; pinned workloads unaffected until updated |
| Server unreachable | calls fail with `tool_unreachable`; breaker (D30) may trip |
| Kill switch and discovery race | kill-switch check wins at the boundary (fail-closed) |

## Security

- Trust is enforced at the request boundary; the agent cannot self-declare a tool safe.
- Third-party output is untrusted by default and always passes shaping + injection scan.
- Server credentials are secret refs resolved at the boundary, never in the catalog (D33).
- Tool calls and denials are audited with tool ID, decision, and rule id (DD-07).

## Testing

- A fixture MCP server connects; its tools are discovered and callable.
- A workload calling a tool outside its allow-list is denied with a reason.
- A destructive tool is blocked for a read-only-only workload.
- A server restart preserves tool IDs; a removed tool fails admission.
- Output flows through shaping and injection scanning; taint is applied.
- The kill switch disables a discovered tool fleet-wide instantly.

## Open Questions

- Should discovery auto-onboard read-only tools, or always require explicit `tools add`?
- How are server fingerprints established for ephemeral stdio processes?
- Should tool chaining (tool calls tool) be allowed, and how does policy recurse?

## See Also

- [MCP Tool Registry Design](mcp-tool-registry-design.md) (D13) — model superseded for v0.2.0
- [Defense & Policy v2 Design](defense-policy-v2-design.md) (D29) — policy, kill switch, injection
- [Runtime Guards Design](runtime-guards-design.md) (D30) — retries and tool breakers
- [Execution Sandbox Design](execution-sandbox-design.md) (D11) — destructive tool isolation
- [PRD 05: Features](../prd/05-features.md) — Tools & MCP
- [WBS Part 10](../wbs/v0.2.0/wbs-v0.2.0-part10-probes-mcp.md) — M44

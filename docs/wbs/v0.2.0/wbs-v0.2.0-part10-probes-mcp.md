# WBS v0.2.0 — Part 10: Probes, Self-Monitoring & MCP v2

**Milestones:** M43–M44 · **Part:** 10 of 19

## Goal

Monitor quality proactively with synthetic probes, give operators approval analytics and plane self-monitoring, and turn the MCP tool layer into a live registry that connects real servers with enforced trust levels.

## M43 — Synthetic Probes, Quality Scores, Approval Analytics & Plane Self-Monitoring

**Objective:** Run periodic ping-tasks that measure live quality/readiness for early drift warning, expose production quality scores and approval analytics, and let the plane observe itself with Prometheus metrics + Grafana dashboards.

**Work items:**

- [x] [#315](https://github.com/deghosal-2026/hiveplane/issues/315) — M43-01 — Synthetic probes: scheduled ping-tasks per workload with known-good expected behavior
- [x] [#316](https://github.com/deghosal-2026/hiveplane/issues/316) — M43-02 — Probe scoring: pass/fail + latency/cost, feeding health and early drift warning
- [x] [#317](https://github.com/deghosal-2026/hiveplane/issues/317) — M43-03 — Probe scheduling/isolation: probes use a separate budget and never affect production outcomes
- [x] [#318](https://github.com/deghosal-2026/hiveplane/issues/318) — M43-04 — Production quality scores surfaced (from M36 online eval) as a first-class health signal
- [x] [#319](https://github.com/deghosal-2026/hiveplane/issues/319) — M43-05 — Approval analytics: latency by approver, bottleneck detection, trends
- [x] [#320](https://github.com/deghosal-2026/hiveplane/issues/320) — M43-06 — Plane self-monitoring: the control plane exports its own Prometheus metrics
- [x] [#321](https://github.com/deghosal-2026/hiveplane/issues/321) — M43-07 — Official Grafana dashboard JSONs shipped for fleet, health, cost, and plane health
- [x] [#322](https://github.com/deghosal-2026/hiveplane/issues/322) — M43-08 — Tests: probe detects seeded decay before drift threshold; analytics compute correctly; self-metrics endpoint exposes expected series

**Test ticket:** [#323](https://github.com/deghosal-2026/hiveplane/issues/323) — Test cases for Synthetic Probes, Quality Scores, Approval Analytics & Plane Self-Monitoring

**Deliverables:**
- `hiveplane.probes` package; probe schedule config
- Plane `/metrics` exporter + Grafana dashboard JSONs under `deploy/grafana/`
- `docs/design/agent-health-slo-design.md`, `docs/observability.md` update

**Acceptance criteria:**
- [x] A synthetic probe flags quality decay before the drift threshold trips
- [x] Probes run on their own budget and cannot deliver results to fan-out
- [x] Approval analytics show per-approver latency and a bottleneck
- [x] The plane's own metrics are scrapeable and the shipped dashboards render
- [x] Production quality scores appear in the health model

**Done when:** quality is monitored proactively, approvals are measurable, and the plane observes itself.

> **Status:** M43 complete. `hiveplane.probes` schedules cheap ping-tasks on a separate capped budget that never deliver to fan-out; a failing probe raises an early drift warning. `hiveplane.health.analytics` computes per-approver latency, the bottleneck, and approve/deny trends; `PlaneMetrics` exports the plane's own Prometheus `/metrics` (decoupled); Grafana dashboards for fleet/health/cost/plane are shipped under `deploy/grafana/dashboards/`. Production quality scores feed the health model (M42). Issues #315–#323 closed; all tests pass with a database, coverage >95%, ruff and mypy strict clean.

**Dependencies:** M42 (health), M36 (quality scores).

**Notes / risks:** probes cost money — keep them cheap and rate-limited. Self-monitoring must not depend on the components it monitors (avoid circular failure).

## M44 — MCP Registry v2 (Live Transport)

**Objective:** Connect real MCP servers, discover tools dynamically, onboard tools with `hiveplane tools add`, and enforce trust levels at the request boundary with manifest allow-lists.

**Work items:**

- [x] [#324](https://github.com/deghosal-2026/hiveplane/issues/324) — M44-01 — Live MCP transport (built on mcp-fabric) — connect to real MCP servers, list/call tools
- [x] [#325](https://github.com/deghosal-2026/hiveplane/issues/325) — M44-02 — Dynamic tool discovery: refresh the tool catalog from connected servers
- [x] [#326](https://github.com/deghosal-2026/hiveplane/issues/326) — M44-03 — `hiveplane tools add|list|show|remove` onboarding with stable tool IDs
- [x] [#327](https://github.com/deghosal-2026/hiveplane/issues/327) — M44-04 — Trust levels (read-only vs. destructive) assigned at onboarding and enforced at the request boundary
- [x] [#328](https://github.com/deghosal-2026/hiveplane/issues/328) — M44-05 — Manifest tool allow-lists: workloads may only call permitted tools
- [x] [#329](https://github.com/deghosal-2026/hiveplane/issues/329) — M44-06 — Tool execution through the boundary (real data, not fabricated), with usage/audit
- [x] [#330](https://github.com/deghosal-2026/hiveplane/issues/330) — M44-07 — Integration with policy (M40) and kill switch (M40-06)
- [x] [#331](https://github.com/deghosal-2026/hiveplane/issues/331) — M44-08 — Tests: connect a fixture MCP server, discover tools, enforce trust level + allow-list, kill-switch a tool

**Test ticket:** [#332](https://github.com/deghosal-2026/hiveplane/issues/332) — Test cases for MCP Registry v2 (Live Transport)

**Deliverables:**
- `hiveplane.mcp` live transport + registry
- Tool onboarding CLI/API
- `docs/design/mcp-registry-v2-design.md` (update)

**Acceptance criteria:**
- [x] A real MCP server connects and its tools are discovered and callable
- [x] A workload calling a tool outside its allow-list is denied with a reason
- [x] A destructive tool is blocked for a workload whose policy allows only read-only tools
- [x] Tool outputs flow through shaping (v0.1.0) and injection scanning (M39)
- [x] The kill switch disables a discovered tool fleet-wide instantly

**Done when:** agents use real MCP tools through the plane with trust levels and allow-lists enforced.

> **Status:** M44 complete. `hiveplane.mcp` provides a live MCP client over stdio and HTTP JSON-RPC (normalized `tool_unreachable`/`tool_error`/`tool_timeout`/`schema_mismatch`), dynamic discovery with stable ULID tool IDs that survive server restarts, and `discovered`/`active`/`absent`/`retired` states. Trust is assigned at onboarding and enforced at the request boundary; manifest allow-lists deny calls with `tool_not_allowed` before policy; every call is executed for real through the boundary (fixture + live composite executor) and flows through shaping and injection scanning; the M40 kill switch wins at the boundary. Ships CLI (`tools add --server`, `tools show|remove`, `mcp servers|tools`), API (`/mcp/servers`, `/mcp/tools`), tables `mcp_servers`/`mcp_tools`/`mcp_tool_versions` (migration `0022`), and a fixture MCP server for tests. Issues #324–#332 closed; 1996 tests pass with a database, coverage 95.02%, ruff and mypy strict clean.

**Dependencies:** M39 (shaping/injection), M40 (policy/kill switch); mcp-fabric.

**Notes / risks:** dynamic discovery can introduce unstable IDs — require stable tool identity and handle server restarts gracefully. Treat third-party tool output as untrusted by default.

## Exit Gate (M43, M44)

- [x] All tests in the system pass: `pytest`
- [x] Code coverage total > 95%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] All relevant docs updated (probes, observability, MCP registry, tool guide)
- [x] All M43–M44 issues done and closed
- [x] Commit and push changes

## See Also

- [PRD 05 Features](../../prd/05-features.md) — Observability & Health, Tools & MCP themes
- [PRD 09 Roadmap](../../prd/09-roadmap.md) — pillars F, G
- [v0.2.0 index](wbs-v0.2.0-index.md)

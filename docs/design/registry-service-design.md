# D3: Registry Service Design

> Status: M3-M4 implemented (`hiveplane.registry`, `/workloads`, `/tools`, `/attestations`). Storage is the in-memory `RegistryStore`; the PostgreSQL-backed store and migrations land in M18 (Part 9).

## Problem

The registry holds desired state: which agents exist, who owns them, what they are allowed to do, and — critically — whether they are certified to operate in production. It is the source of truth the rest of the control plane reconciles against. After the PRD rewrite, the registry must also enforce certification status at admission, store and retrieve attestations, manage trigger rules, require re-certification on manifest updates, and store MCP tool registry entries.

See [PRD 02: Architecture](../prd/02-architecture.md) § Registry Service and [PRD 05: Features](../prd/05-features.md) § Workload Model & Registry.

## Responsibilities

- store and validate workload manifests (including certification, triggers, sandbox, output shaping, tools, fan-out, health)
- enforce certification status at admission — refuse to admit a workload to a production context unless its `certification.status` is `certified` and the attestation is valid and unexpired (DD-09, T8)
- store and retrieve signed attestations (immutable, signed, verifiable) (DD-10, T9)
- store trigger rules per workload (webhook/alert/PR/cron)
- require re-certification on manifest update — manifest changes to `spec.runtime`, `spec.model`, `spec.tools`, or `spec.certification.benchmark_corpus` trigger a re-certification requirement before promotion (DD-11)
- store MCP tool registry entries — tool definitions with stable IDs, trust levels, and classification
- expose the fleet catalog (list, get, by owner, by team, by certification status)
- version manifests and record change history (append-only)
- reject invalid or conflicting definitions

## Certification Status Enforcement

The registry is the enforcement point for the certification gate (DD-09). When the Execution API or Trigger & Ingress Service requests admission for a run:

| Target Context | Required Status | Behavior on Failure |
|----------------|----------------|---------------------|
| `production` | `certified` (valid, unexpired attestation) | Run refused; caller receives `refused: certification` |
| `staging` | `provisional` or `certified` | Run admitted to staging only |
| `sandbox` | `uncertified`, `provisional`, or `certified` | Run admitted to sandbox context |

### Attestation Verification

On every production admission, the registry:

1. Retrieves the attestation by `attestation_id`.
2. Verifies the attestation signature (key-paired; verification on read) (T9).
3. Checks that `certified_at` + `re_cert_interval` has not expired (i.e., `expires_at` is in the future).
4. Checks that the model identity in the attestation matches the runtime model identity (T11, DD-10).
5. If any check fails, admission is refused and the workload's status may be downgraded to `quarantined`.

## Attestation Storage

Attestations are stored as immutable, signed records:

| Field | Contents |
|-------|----------|
| `attestation_id` | Unique identifier |
| `workload` | Workload name |
| `manifest_version` | Manifest version at certification time |
| `benchmark_corpus` | Corpus reference and version |
| `model_identity` | Provider, family, version (bound to certification) |
| `eval_results` | Per-task pass/fail, pass rate, critical failures, latency |
| `threshold` | Production or staging threshold used |
| `status_assigned` | `provisional` or `certified` |
| `timestamp` | Certification timestamp |
| `environment` | Benchmark environment (sandbox config, adapter version) |
| `signer` | Identity that signed the attestation |
| `signature` | Cryptographic signature |
| `previous_attestation_id` | Link to previous certification (for regression diff) |

Attestations are append-only — a new certification creates a new attestation; old ones are never mutated. This supports regression diffs and audit trails.

## Re-Certification on Manifest Update

When a manifest is updated (PUT), the registry determines whether re-certification is required:

| Changed Field | Re-Certification Required? | Reason |
|---------------|---------------------------|--------|
| `spec.runtime` (adapter, entrypoint) | Yes | Orchestration change can regress |
| `spec.model` (identity, strategy) | Yes | Model swap changes behavior (T11) |
| `spec.tools` (allow/deny, trust levels) | Yes | Tool access change affects behavior |
| `spec.certification.benchmark_corpus` | Yes | Corpus change requires re-eval |
| `spec.budget` | No | Budget is a runtime constraint, not a behavioral property |
| `spec.approvals` | No | Approval config is a runtime constraint |
| `spec.triggers` | No | Trigger config affects when runs start, not agent behavior |
| `spec.fan_out` | No | Delivery config, not behavioral |
| `spec.output_shaping` | No | Output governance, not behavioral |
| `spec.health` | No | Health config, not behavioral |
| `metadata.labels` | No | Metadata only |

If re-certification is required, the manifest update is stored as a **pending version**. The workload's `certification.status` is reset to `uncertified` for the new version. The old version remains active and certified until the new version passes re-certification and is promoted (DD-11). If the new certification shows a regression (pass rate dropped or critical task failed), promotion is blocked and the author receives a replayable trace.

## MCP Tool Registry Storage

The registry stores MCP tool definitions:

| Field | Contents |
|-------|----------|
| `tool_id` | Stable identifier (e.g., `mcp.github.read_issue`) |
| `name` | Human-readable name |
| `mcp_server` | MCP server name/endpoint |
| `trust_level` | `read_only` or `destructive` |
| `description` | What the tool does |
| `parameters_schema` | JSON Schema for tool parameters |
| `registered_at` | Timestamp |
| `registered_by` | Identity |

Workloads reference tools by `tool_id`. The registry validates that all `spec.tools.allow` entries reference registered tools.

## API Sketch

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/workloads` | Register a workload (validates manifest, triggers initial certification) |
| GET | `/workloads` | List workloads (filter by owner, team, certification status) |
| GET | `/workloads/{name}` | Get a workload (current version + certification status) |
| PUT | `/workloads/{name}` | Update a workload (triggers re-certification if behavioral fields changed) |
| DELETE | `/workloads/{name}` | Deregister (blocked if active runs) |
| GET | `/workloads/{name}/versions` | Manifest history (append-only) |
| GET | `/workloads/{name}/certification` | Current certification status + attestation summary |
| GET | `/workloads/{name}/attestations` | List all attestations for the workload |
| GET | `/attestations/{id}` | Retrieve a specific attestation (with signature verification) |
| POST | `/workloads/{name}/certify` | Trigger a certification run (benchmark runner) |
| GET | `/workloads/{name}/certifications/compare?v1={id}&v2={id}` | Regression diff between two certifications |
| POST | `/workloads/{name}/promote` | Promote a pending version (blocked unless re-certified) |
| GET | `/workloads/{name}/triggers` | List trigger rules for the workload |
| POST | `/workloads/{name}/triggers` | Add a trigger rule |
| DELETE | `/workloads/{name}/triggers/{trigger_id}` | Remove a trigger rule |
| GET | `/tools` | List MCP tools (filter by trust level, MCP server) |
| POST | `/tools` | Register an MCP tool |
| GET | `/tools/{tool_id}` | Get a tool definition |
| PUT | `/tools/{tool_id}` | Update a tool definition (may trigger re-certification for workloads using it) |
| DELETE | `/tools/{tool_id}` | Deregister a tool (blocked if referenced by active workloads) |
| GET | `/workloads/{name}/admission?context={ctx}` | Check admission status for a target context |

## Data

- `workloads` — current manifest, owner, team, certification status, timestamps
- `workload_versions` — append-only manifest history (including pending versions)
- `certifications` — certification records (status, threshold, results summary, timestamps)
- `attestations` — immutable signed attestation records (see Attestation Storage above)
- `tools` — MCP tool registry entries
- `trigger_rules` — trigger rules per workload (type, match, mode, max_concurrent)
- `drift_schedules` — re-certification schedules per workload (interval, last run, next run)
- uniqueness on `workloads.name`; ownership metadata indexed for fleet review
- index on `workloads.certification_status` for fleet queries
- index on `attestations.workload` for attestation history

## Open Questions

- deletion semantics when runs reference a workload
- whether manifests are stored whole or normalized
- attestation key management (who holds the signing key, rotation strategy)
- whether tool updates should auto-trigger re-certification for all referencing workloads or just flag them
- pending version retention policy (how long before a pending version is garbage-collected)

## See Also

- [Workload manifest](workload-manifest-design.md) — full manifest shape including certification, triggers, sandbox, tools
- [Run lifecycle](run-lifecycle-design.md) — admission flow, certification check, trigger-originated runs
- [Policy engine](policy-engine-design.md) — how tool trust levels are consumed in policy decisions
- [State store](state-store-design.md) — entity definitions for certifications, attestations, tools, trigger_rules, drift_schedules
- [Telemetry](telemetry-design.md) — certification metrics
- [Design decisions](design-decisions.md) — DD-01, DD-09, DD-10, DD-11

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - Unreleased

The Complete Fleet OS release: tenancy, autonomy (triggers, pipelines, GitOps), the
immune system (drift, quarantine, promotion gate), and fleet scale-out. In progress.

### Added (M25-01 — tenancy foundation)

- **Tenancy package (`hiveplane.tenancy`)** — `Tenant`/`Team`/`Membership` domain models
  (`Role`: admin/approver/viewer), `TenantScopeError`, and a frozen `TenantContext`
  (`SYSTEM_CONTEXT`, `DEFAULT_CONTEXT`) threaded explicitly through every store call.
  Reserved ids: `default` (legacy backfill) and `system`.
- **Tenant store** — protocol + in-memory + PostgreSQL implementations for
  tenants/teams/memberships, tenant-qualified uniqueness, and composite team identity
  `(tenant_id, team_id)`.
- **Tenant-scoped schema** — every existing table gains a non-null `tenant_id` (plus
  `team_id`/`attribution_key` on run/usage/cost tables); composite `(parent_id, tenant_id)`
  foreign keys and tenant-qualified unique constraints throughout.
- **Migration `0003`** — forward-only: seeds `default`/`system` tenants, backfills legacy
  rows to `default`, deletes orphan runs and dangling workload references, then enforces
  the new constraints. Auto-migration on startup preserved; verified against Postgres 16
  on fresh and real v0.1.0 databases.
- **Store-layer isolation** — every durable store (runs, registry, certification, budget,
  approvals, policy packs, audit) enforces tenant scoping: reads outside the acting tenant
  look like the record does not exist, and writes that cross a tenant boundary raise
  `TenantScopeError`. Runs are attributed to the submitting tenant.
- **API tenant resolution** — `X-Hiveplane-Tenant` / `X-Hiveplane-Team` headers select the
  acting tenant (default tenant when absent). This is plumbing, not an auth boundary;
  signed identities arrive in M45.
- **Test database override** — `HIVEPLANE_DATABASE__*` env vars are honored by the
  Postgres-gated tests so they can run against a throwaway database (as CI does).

### Changed

- Version bumped to 0.2.0 (in-progress release line).

### Added (M25-02..M25-11 — fleet-control data models)

- **`hiveplane.fleet`** — the frozen v0.2.0 model surface: triggers (source,
  event filter, dedup, cooldown, rate limit, task template, admission rule,
  events/runs/DLQ), pipelines (validated DAG with edges, handoff mappings,
  per-step gates, budget, run linkage), versioned policy packs with lint status
  and policy decision records, secrets with rotation metadata and injection
  references, distributed workers with leases and heartbeats, artifacts with
  retention policies, metering events and day/week/month cost periods, and
  desired-state reconciliation (desired specs, reconcile state, drift records).
- **20 tenant-scoped tables** for the above, with composite foreign keys where a
  real parent-child relationship exists and tenant-qualified uniques.
- **Migration `0004`** — forward-only and idempotent; auto-migration on startup
  preserved. Verified up/down against PostgreSQL 16.
- **Tests** — model validation (malformed trigger/pipeline/policy definitions
  rejected with actionable errors), schema coverage, tenant scoping, and
  migration up/down. Coverage ≥ 95% with a database (as CI runs).

### Added (M26 — desired-state reconciliation / GitOps)

- **`hiveplane.reconcile` package** — the Kubernetes-defining controller:
  - **Loader** (`loader`) — validates a fleet manifest set (workloads, policy packs,
    triggers, budgets) from a local directory or a read-only git source pinned to a ref;
    a bad revision is rejected whole with nothing applied and content hashes are
    deterministic across formatting.
  - **Observer / differ / conflict policy** — reads actual state through existing
    services and diffs it field by field. Declarative `spec.*` fields are
    declared-wins; runtime, certification-status, and operator-pinned fields are
    observed-wins and recorded as drift rather than overwritten.
  - **Planner + guardrails** — classifies actions additive/soft/destructive and gates
    destructive ones: they require explicit permission, an empty desired set cannot
    cascade to mass deregistration, the first reconcile needs confirmation, and
    destructive actions are rate-limited per pass.
  - **Executor** — applies actions only through `RegistryService` (register, update,
    re-certify, deregister, quarantine, policy-version enforcement), so reconcile
    cannot bypass certification, policy, or budget enforcement.
  - **Controller** — one pass is observe → diff → plan → act → record, with a dry-run
    `plan` mode that mutates nothing.
- **Single-writer safety** — per-source in-memory lock and a PostgreSQL advisory lock
  keyed by source id, so a second replica stays passive and cannot double-act.
- **Git change detection** — poll-on-revision-change and HMAC-SHA256 webhook triggers;
  git credentials resolve from a secret reference, never inline, and the source is
  never written back to.
- **Migration `0005`** — append-only `reconcile_runs` history table (revision, mode,
  outcome, counts, timestamps); forward-only and idempotent.
- **API + CLI** — `GET /reconcile/{source}` (status), `/runs`, `/drift`,
  `POST /reconcile/{source}/plan|apply`, and `hiveplane reconcile status|plan|apply`.
- **Tests** — idempotency, add/remove/update paths, dry-run safety, concurrent-controller
  safety, guardrails, conflict policy, and Postgres store/migration round-trips.
  Coverage ≥ 95% with a database.

### Added (M27 — trigger service core)

- **`hiveplane.triggers` package** — the autonomy core that lets external events
  start runs safely:
  - **Strict DSL** (`schema`) — typed trigger documents (source, target, event
    filter, task template, dedup, cooldown, rate limit, admission rule, cron
    schedule/timezone/missed-schedule policy); unknown fields are errors.
  - **Webhook ingest** (`ingest`) — HMAC-SHA256 over the raw body with a
    `timestamp + nonce` replay window; bad/missing signatures and replays are
    rejected with a status code and audited.
  - **Cron engine** (`cron`, `scheduler`) — five-field cron parsing/matching,
    timezone-aware next-fire, and `skip`/`catch_up`/`catch_up_all` missed-schedule
    policies; DST fall-back fires once, spring-forward does not crash.
  - **Dedup/cooldown/rate/backpressure** (`limiter`) — dedup keys and cooldowns
    read persisted event history; a per-trigger token bucket returns a retryable
    rate rejection and a global bucket protects the plane under a storm.
  - **Templating** (`templating`) — typed, value-only `{{ event.path }}`
    substitutions with per-field and total byte limits; no expression language,
    no code, injection-safe by construction.
  - **Engine** (`engine`) — evaluate → limit → render → admit → idempotently
    submit through `RunService`, recording the event, its run linkage, and
    `blocked_cert`/`blocked_admission`/`failed` outcomes. Admission never bypasses
    certification. Runs carry `trigger_origin` attribution.
- **Trigger store** — memory + PostgreSQL implementations for declarations,
  events, runs, and the DLQ; migration `0006` adds `trigger_nonces` for durable
  replay protection.
- **API** — `POST /triggers` and CRUD, `POST /triggers/{id}/enable|disable`,
  `POST /triggers/webhook/{id}`, and `GET /triggers/{id}/events|runs` plus
  `GET /triggers/dlq`.
- **Tests** — signature/replay, dedup/cooldown/rate/backpressure, cron (incl.
  DST), templating safety, idempotent submission, and Postgres store/migration
  round-trips. Coverage ≥ 95% with a database.

### Added (M28 — trigger sources, admission & history)

- **GitHub source** — verifies `X-Hub-Signature-256`, matches the declared
  event/action/repo filter, normalizes the PR/issue number, and treats a
  `/hiveplane` PR comment as a re-trigger.
- **Alertmanager source** — one event per alert with the alert `fingerprint` as
  the dedup key; firing/resolved filtering and optional body HMAC.
- **Watch mode** — scheduled 24/7 operators (`WatchRunner`) that skip a tick
  while a prior watch run is active, recording `skipped_concurrent`.
- **Admission rules** — `staging-auto` auto-admits staging; `gated` submits to
  production with a forced approval (held, paused) while still enforcing
  certification, model, budget, and policy gates; `deny` refuses. Runs carry
  `trigger_origin` attribution.
- **Freeze windows** — scoped (tenant/team/workload) `trigger_freezes` windows
  (migration `0007`) suppress matching events (`suppressed_freeze`) and drain
  in-flight runs gracefully (pause) or with `drain: abort` (stop), attributed to
  the declaring operator.
- **History & audit** — every evaluation, suppression, admission, and rejection
  records an outcome and a reason on the event, and is audited.
- **DLQ replay** — `POST /triggers/dlq/{id}/replay` re-drives a parked delivery
  with a fresh event id (re-checking dedup/cooldown), marks it replayed, and
  audits the action.
- **API** — `POST /triggers/github/{id}`, `/alertmanager/{id}`, `/{id}/test`,
  and freeze CRUD (`GET/POST /triggers/freezes`, `DELETE /triggers/freezes/{id}`).
- **CLI** — `hiveplane triggers list|show|create|test|replay|enable|disable`.
- **Tests** — each source end-to-end, admission gating, freeze suppression/drain,
  and DLQ replay. Coverage ≥ 95% with a database.

### Added (M29 — multi-agent pipelines)

- **`hiveplane.pipelines` package** — the pipeline runtime:
  - **Spec DSL** (`spec`) — a validated DAG of `workload`/`fan_out`/`fan_in`/
    `gate`/`transform` nodes with edges, handoff mappings, `on_failure`, `retry`,
    per-step approval, per-step budget, and a cumulative budget. Cycles are
    rejected with Kahn's algorithm, naming the offending nodes.
  - **Handoff layer** (`handoff`) — restricted `${node.output.path}` resolution
    and producer/consumer schema validation from a workload's `spec.io`; a
    mismatch fails the node before a partial input reaches an agent.
  - **Engine** (`engine`) — topological execution over child runs carrying a
    `PipelineOrigin`; parent state is derived from node records. Supports
    fan-out/fan-in (with `json_merge`/`concat`/`sum`/`first_success` reducers),
    `requires_approval: before` and `gate` nodes on the approval queue, budget
    pause/fail on `on_exceed`, per-step overrides, `fail_fast`/`continue`, node
    retry, and a derived timeline (status/cost/artifacts/attempts).
  - **Store** (`store`) — specs and run headers/node runs (memory + Postgres);
    migration `0008` adds `pipeline_run_headers` and `pipeline_node_runs`.
  - **Adapters** (`executor`) — `RunNodeExecutor` submits nodes as child runs;
    `ServiceApprovalGate` reuses the approval queue.
- **Run lifecycle** — `Run.pipeline_origin` and `RunService.submit(pipeline_origin=...)`
  attribute child runs to their pipeline node; `WorkloadSpec.io` declares handoff
  schemas.
- **API + CLI** — `/pipelines` CRUD, `/pipelines/{id}/runs`, `/pipeline-runs/{id}`,
  node retry; `hiveplane pipelines list|show|submit|status|retry`.
- **Tests** — linear end-to-end, cycle rejection, handoff mismatch, gates,
  fan-out/in, budget, `fail_fast`/`continue`, retry, and Postgres round-trips.
  Coverage ≥ 95% with a database.

### Added (M30 — smart task router & agent-as-tool)

- **`hiveplane.router` package** — routes a plain-language task to the best
  certified workload:
  - **Certified catalog** (`catalog`) — only workloads admitted for the target
    context are candidates; uncertified workloads are never offered.
  - **Cheap classifier** (`classifier`) — `LLMTaskClassifier` scores candidates
    through the provider seam and parses/normalizes the JSON response.
  - **Engine** (`engine`) — chooses the top candidate only when it clears the
    confidence threshold **and** beats the runner-up by the margin; otherwise it
    refuses (`low_confidence`/`ambiguous`) with ranked candidates rather than
    guessing. The raw task is never stored — only its digest.
  - **Store** (`store`) — router decisions (memory + Postgres, migration `0009`);
    every route is explainable from recorded candidate scores.
- **`hiveplane.agent_tools` package** — exposes certified workloads as
  `agent.<workload>` tools:
  - **Registry** (`registry`) — resolves tool ids and lists only certified tools.
  - **Invoker** (`engine`) — submits nested child runs carrying an
    `AgentToolOrigin`; enforces `max_agent_depth`, rejects chain cycles and
    budget exhaustion, and lets nested admission enforce certification/policy.
  - **Store** (`store`) — invocation records (memory + Postgres, migration `0009`).
- **Run lifecycle** — `Run.agent_tool_origin` and
  `RunService.submit(agent_tool_origin=...)` attribute nested calls.
- **A2A interop (stretch)** — `hiveplane.a2a` exposes certified workloads as A2A
  agent cards and maps inbound tasks onto the router and run lifecycle, behind
  `HIVEPLANE_A2A__ENABLED`; endpoints are unmounted when the flag is off.
- **API + CLI** — `POST /route`, `GET /routes[/{id}]`, `GET /agent-tools`,
  `POST /agent-tools/{id}/invoke`, and (flagged) `/a2a/agents`, `/a2a/tasks`;
  `hiveplane route "<task>"` and `hiveplane agents list|invoke`.
- **Tests** — routing accuracy on a labeled set, low-confidence refusal,
  uncertified-never-a-candidate, nested-call propagation, depth/cycle rejection,
  A2A trust/refusal, and Postgres round-trips. Coverage ≥ 95% with a database.

### Added (M31 — runtime adapters v2 & `hiveplane wrap`)

- **Adapter contract v2** — the boundary is explicit and versioned:
  `AdapterCapabilities`, `AdapterEvent`, `CONTRACT_VERSION`, and extended
  `Adapter` methods (`capabilities()`, `stream()`, `model_identity()`,
  `conformance_version()`). `spec.runtime.adapter_contract` records the expected
  version (default `2`); `*_of` helpers tolerate pre-v2 adapters.
- **Reference adapters bumped to v2** — raw-worker and LangGraph report
  capabilities, an ordered (buffered) event stream, and the model identity
  captured from actual inference via `WorkerContext`.
- **PydanticAI adapter** (`hiveplane.adapters.pydanticai`) — wraps
  `pydantic_ai.Agent`; a governed custom `Model` routes inference through
  `WorkerContext.complete`. Extra: `hiveplane[pydantic-ai]`.
- **OpenAI Agents SDK adapter** (`hiveplane.adapters.openai_agents`) — wraps
  `agents.Agent`; the same governed seam. Extra: `hiveplane[openai-agents]`.
- **Conformance suite v2** (`tests/conformance.py`) — every adapter passes
  lifecycle, streaming, capabilities, and inference-captured identity; pause/resume
  is negotiated (`pause_resume=False` adapters skip the hold scenarios).
- **Import-boundary test** — an AST scan fails if a framework/provider SDK leaks
  into core (DD-02); only `hiveplane.adapters` and `hiveplane.llm` may import them.
- **`hiveplane wrap`** — AST-only framework detection and scaffold generation:
  emits `workload.yaml` (uncertified draft), `adapter_scaffold.py`,
  `corpus.template.yaml`, and `README.md` into a new `--out` directory. It never
  imports/executes the app and never writes to the source tree; generated files
  are inert until registered and certified. CLI: `hiveplane wrap <path>`.
- **Adapter introspection** — `GET /adapters`, `GET /adapters/{name}` and
  `hiveplane adapters list` report each adapter's contract version and capabilities.
- **Tests** — conformance for all four adapters, wrap round-trip (source tree
  unchanged, generated manifest valid, uncertified stays uncertified), import
  boundary, and the adapters API. Coverage ≥ 95% with a database.

### Added (M32 — promotion gate & re-certification)

- **Artifact binding** (`hiveplane.certification.binding`) — `ArtifactBinding`
  and `compute_binding`: one `artifact_hash` over canonical JSON of the
  behavior-affecting spec, the sorted toolset, the exact canonical model
  identity, and the resolved policy version. It excludes the certification block
  and identity metadata, so applying an attestation never invalidates it and an
  owner change never forces a re-cert. `changed_bindings` names the exact
  components that changed.
- **Certification bound to the hash** — `Attestation.artifact_hash` and
  `Attestation.binding`; `CertificationService` computes them (with an optional
  policy-version lookup).
- **Promotion gate** (`hiveplane.certification.promotion`) — `PromotionGate`
  requires the current version to be `certified` with a valid, unexpired
  production attestation for the target artifact hash; otherwise it refuses,
  naming the changed binding(s). `recertify_and_promote` runs the benchmark for
  the current artifact, then promotes.
- **Automatic invalidation** — when a manifest version changes a certified
  workload's artifact hash, the registry marks it `uncertified` and requires
  re-certification (`WorkloadRecord.artifact_hash`,
  `RegistryService.mark_uncertified_for_production`).
- **Store + migration** — `PromotionStore` (memory + Postgres); migration `0010`
  adds `promotions`.
- **API + CLI** — `POST /promotions`, `POST /promotions/recertify`,
  `GET /promotions[/{id}]`; `hiveplane promote <workload> --to production
  [--version N] [--recertify]`.
- **Audit** — every promotion attempt is recorded (`promotion.admitted` /
  `promotion.refused`) with the workload, version, hash, and reason.
- **Tests** — unchanged certified promotes; changed manifest/toolset/model
  blocks and names the binding; uncertified never promotes; automatic
  invalidation; re-certification orchestration; audit; Postgres round-trip and
  migration `0010`. Coverage ≥ 95% with a database.

### Added (M33 — regression diff & certification compare)

- **Diff engine** (`hiveplane.certification.diff`) — task-level `pass→fail` /
  `fail→pass` with latency, token, and cost deltas; `Severity` (critical/warning)
  driven by critical tasks and threshold rules; removed passing tasks block.
- **Replayable traces** — every changed task carries a `ReplayFrameSet` (task
  contract + trace id + stable replay ref) built from the corpus, so a regressed
  task can be replayed deterministically (D18).
- **Baseline selection** — `CertificationCoordinator.compare_to_baseline` uses
  the last `certified` record unless an explicit `baseline_id` is given;
  comparison is deterministic.
- **Regression report** — a `RegressionReport` (machine JSON + human summary) is
  attached to the certification record when re-certification runs; critical
  regressions feed the promotion gate's refusal reason.
- **API + CLI** — `GET /certifications/compare-baseline/{after}`;
  `hiveplane certs compare <v1> <v2> [--json]` and
  `hiveplane certs compare-baseline <workload> <after> [--baseline <id>] [--json]`.
- **Tests** — seeded regressions pinpointed with metric deltas and replay frames,
  identical results diff empty, non-critical vs critical severity, baseline
  determinism, report attachment, and the API/CLI. Coverage ≥ 95% with a
  database.

### Added (M34 — drift detector, auto-quarantine & reinstatement)

- **Drift scheduler** (`hiveplane.drift.DriftScheduler`) — per-workload
  re-certification cadence (manifest `re_cert_interval`, fleet default fallback)
  with a deterministic `due()`, plus certification expiry/renewal states
  (`valid`/`expiring`/`expired`). Expired certifications are not admissible.
- **Drift detector** — compares a fresh evaluation to the certified baseline
  (pass-rate drop and new failures vs. configured thresholds and critical-failure
  rules); a strong (≥2×) signal or critical failures escalate severity.
- **False-positive controls** — a single exceeding run is a warning; quarantine
  requires N consecutive exceeding runs (`drift.required_consecutive_failures`)
  or a strong signal, so a stable agent is never falsely quarantined.
- **Auto-quarantine** (`hiveplane.drift.QuarantineService`) — forces status
  `quarantined`, immediately revoking production admission, optionally cancels
  in-flight runs per policy, persists a `QuarantineRecord` (reason + evidence),
  notifies the owner via Slack/webhook fan-out (D15), and audits the action.
- **History & dashboard** — quarantine records and drift assessments persist
  (`quarantines`, `drift_assessments`; migration `0011`) and surface on the
  certification dashboard with reason and severity.
- **Reinstatement** — `hiveplane.drift.ReinstatementService` requires a fresh
  passing certification (staging recovery → production certification) and
  re-granted admission before reinstating; every step is audited.
- **API + CLI** — `GET /drift/{due,schedules,expiries}`, `POST /drift/{assess,probe}`,
  `GET|POST /quarantines`, `POST /quarantines/{id}/reinstate`; and
  `hiveplane drift {due,schedules,expiries,assess,probe,quarantine,quarantines,reinstate}`.
- **Tests** — detector thresholds/trends, false-positive controls, scheduler
  cadence and expiry, quarantine admission revocation + notification + audit,
   reinstatement success/refusal, Postgres round-trip and migration `0011`.
   Coverage ≥ 95% with a database.

### Added (M39 — injection defense & egress allow-lists)

- **Injection scanner** (`hiveplane.defense.DefenseScanner`) — deterministic,
  versioned detectors (`detector_set_version`) for instruction override,
  instruction smuggling (zero-width chars, encoded blobs), tool-call hijack,
  exfiltration intent, and role manipulation. No model calls: the same input
  always yields the same block.
- **Configurable detectors** — per policy pack enable/disable, a severity
  threshold, benign-phrase allow-lists, and `escalate_to_block`; a block carries
  detector id, version, span, severity, and reason.
- **Boundary scanning** — tool output is scanned at the tool-call boundary
  independent of `spec.output_shaping`; a block returns `blocked_injection`
  with rule `injection.scan` and never reaches agent context.
- **Taint marks & provenance** (`hiveplane.defense.TaintTracker`) — untrusted
  tool/trigger output is marked and propagated by union to derived values; a
  destructive tool call is denied (`taint.block`) while untrusted input is live,
  unless the tool declares `allow_untrusted: true`.
- **Egress allow-lists** (`hiveplane.defense.EgressPolicy`, `spec.sandbox.network`)
  — deny-by-default, host + optional port, `*.suffix` wildcards, explicit deny
  wins, cloud-metadata always blocked. Port-aware enforcement at the tool-call
  boundary; every denial is audited (`egress.denied`).
- **Security events** (`hiveplane.defense.events`) — append-only, tenant-scoped
  `security_events` (injection/egress_denied/taint_block/repeated_attempt),
  surfaced by `GET /security/events`; migration `0014_security_events`.
- **Repeated-attempt escalation** — attempts counted per workload over a rolling
  window; crossing `defense.repeat_threshold` (default 3) quarantines the
  workload through the shared M34 immune machinery.
- **Tests** — scanner determinism/versioning/config, taint propagation and
  destructive gating, egress ports/wildcards/legacy spec, security-event store
  (memory + Postgres), tool-boundary integration, repeated-attempt quarantine,
  and an end-to-end seeded-injection/taint/egress flow through `create_app`.

### Added (M35 — attestation transparency, public verification & workload provenance)

- **Transparency log** (`hiveplane.transparency.TransparencyLog`) — append-only,
  hash-chained history of every certification
  (`entry_hash = sha256(prev_hash ‖ seq ‖ attestation_id ‖ canonical_json)`);
  `verify_chain()` detects edits, reorders, and deletions; `prove()` returns
  inclusion evidence. Store protocol + in-memory + PostgreSQL (`attestation_log`;
  migration `0012`). Every certification now appends to the log.
- **Public verification** — unauthenticated `GET /attestations/{id}/verify`
  returns only public evidence (validity, `signer_key_id`, `issued_at`, status,
  log position, chain validity) and never workload internals, corpus contents, or
  secrets. `hiveplane verify <attestation_id>` wraps it (non-zero exit when invalid).
- **Workload provenance & signing** (`hiveplane.transparency.provenance`) — an
  agent bundle binds the manifest identity and entrypoint digest; signed at
  registration and verified at production admission. A tampered digest, signature,
  or swapped manifest **fails production admission**. Export/import envelopes carry
  the signature; `import_bundle` refuses tampered imports.
- **Key management** (`hiveplane.transparency.SigningKeyRegistry`) — persistent
  public keys by `key_id`, rotation that retires the previous key while retaining
  it, and verification-key distribution (`signing_keys`; migration `0013`). Old
  attestations and bundles still verify after rotation.
- **Fan-out verification links** — result notifications include a
  `verification_url` pointing at the public verify endpoint when
  `certification.public_verification_base_url` is configured.
- **Tests** — chain integrity/tamper detection, forged/tampered attestation and
  bundle rejection, public verify unauthenticated + field minimisation, bundle
  export/import refusal, key rotation retaining old keys, Postgres round-trips,
  and migrations `0012`/`0013`.

### Added (M36 — production feedback, corpus learning & online eval)

- **Run feedback** — `hiveplane.learning.FeedbackService` records attributable
  operator feedback (`good` / `bad` / `failed-with-lesson` + notes) on terminal
  runs via UI, CLI (`hiveplane feedback`), and API (`POST /runs/{id}/feedback`).
  `run_feedback` persists (migration `0014`).
- **Feedback → corpus candidates** — a `failed-with-lesson` run auto-proposes an
  inert `CorpusCandidate` (input = run task; expected outcome from the lesson or a
  judge hint). Candidates are reviewable at `GET /corpus/candidates`
  (`corpus_candidates`, `candidate_reviews`; migration `0015`).
- **Mandatory review gate** — `CandidateService.approve`/`reject` are the only
  path to a benchmark task; rejection archives with a reason; rejected/pending
  candidates never enter the corpus. CLI `hiveplane corpus {candidates,approve,reject}`.
- **Corpus versioning** — approved candidates are staged into the next corpus
  version (`CorpusVersionService.integrate`, immutable `corpus_versions`;
  migration `0016`) and the coordinator runs the integrated corpus on the next
  certification, binding the new `corpus_version`.
- **Online eval sampling** — a configurable percentage of production runs is
  sampled deterministically (`sha256(run_id) mod 100 < sample_rate`) for scoring;
  runs marked PII or matching sensitive patterns, and runs past the judge cost cap,
  are skipped. `RunService` samples+judges on terminal production runs.
- **LLM judge & quality signal** — `RubricJudge` scores runs against a versioned,
  immutable rubric (recording the rubric version on every score); `EvalService`
  rolls scores into a rolling-window production quality score with a `dip` flag.
  API `GET /eval/samples`, `GET /workloads/{id}/quality`; CLI `hiveplane eval
  {samples,quality}` (`eval_samples`, `judge_scores`, `rubrics`; migration `0017`).
- **Tests** — feedback capture + auto-candidate, review-gate refusal, corpus
  integration into the next certification, deterministic sampling + PII/cost
  guardrails, judge parsing/clamping, quality dip detection, UI/CLI/API surfaces,
  Postgres round-trips, and migrations `0014`–`0017`.

### Added (M37 — shadow runs)

- **Shadow runs** (`hiveplane.progressive`) — a candidate is executed on the
  **same task** as a paired production run and never delivered: `Run` gains
  `shadow_of` (suppresses fan-out on terminal) and `read_only` (the tool gateway
  hard-blocks side-effecting calls). `RunShadowRunner` submits the candidate in
  the `sandbox` context; `ShadowService` records the outcome and produces an
  **outcome diff** (output, tool calls, cost, latency, policy decisions).
  Separate, capped shadow budget (`shadow_runs`; migration `0019`).
- **API + CLI** — `POST /shadow`, `GET /shadow/{id}/report`; `hiveplane shadow report`.

### Added (M38 — canary routing, auto-promote & model experiments)

- **Canary routing** — `CanaryService` splits eligible triggers deterministically
  (`sha256(run_id) mod 100 < traffic_pct`) to a candidate version while the
  certified baseline serves the rest, bounded by an eligibility rule and a
  **blast-radius cap** (`canary_rollouts`, `canary_samples`; migration `0020`).
- **Evaluation & auto-decision** — candidate vs. baseline error rates and sampled
  judge means over a window with a **minimum-sample** guardrail; a clean window
  **auto-promotes** (re-points traffic) and a regression **auto-aborts** (rolls
  back to the baseline and marks the candidate quarantined). Manual override is
  always available; every transition is audited (actor = `progressive-delivery`).
- **Model experiments** — `ExperimentService` routes across ≥2 model
  configurations, records each arm's benchmark score, and selects the
  highest-scoring completed arm with recorded rationale
  (`experiment_campaigns`, `experiment_arms`).
- **API + CLI** — `/canary*`, `/experiments*`; `hiveplane canary
  start|status|promote|abort`, `hiveplane experiment start`.

### Added (M40 — context-aware policy, what-if, packs, time windows & kill switch)

- **Context-aware policy engine** — decision inputs now include budget state
  (`budget_exhausted`), taint (`taint_untrusted`), and time; a decision carries
  the originating rule, `pack_version`, `detector_set_version`, blast radius,
  and certification.
- **What-if / dry-run** — `PolicyEngine.evaluate(context, dry_run=True)` runs the
  identical code path and marks the decision `dry_run`; `POST /policy/evaluate
  {dry_run}` and the response are conformance-tested for parity.
- **Team policy packs** (`hiveplane.policy.PolicyPackRegistry`) — inheritable,
  versioned packs with `lint` (unknown parents, cycles), immutable `publish`, and
  `apply` that pins the resolved chain to a team (parents first).
- **Time-window policies** — `spec.time_windows` on the manifest (business-hours
  allow windows and blackout calendars) enforced by the engine against the policy
  clock, with `outside_time_window` / `blackout` reasons.
- **Tool kill switch** (`hiveplane.policy.KillSwitch`) — instant fleet-wide
  disable checked at the tool-call boundary before policy; audited disable/re-enable
  (`tool_kill_switch`; migration `0021`). API `/tools/{id}/disable|enable`,
  `GET /tools/disabled`; CLI `hiveplane tools disable|enable`.
- **API + CLI** — `POST /policy/evaluate`, `/policy-packs/{name}/apply`; CLI
  `hiveplane policies lint|publish|apply`.

### Added (M41 — context budget, spend velocity, retries & circuit breakers)

- **Context-window budget** (`hiveplane.guards`) — a fourth runtime budget tracked
  live from real provider token counts; per-step accounting; a breach pauses the
  run cleanly (no crash, no silent truncation) and records the accounting.
- **Spend-velocity guard** — a rolling-window burn-rate guard pauses a workload
  that is spending anomalously before it exhausts its budget, with the observed
  rate and projected time-to-exhaustion.
- **Retry policies** — per-workload/tool exponential backoff with optional full
  jitter, clamped and capped at `max_attempts`.
- **Circuit breakers** — per-tool (and per-workload) breakers trip on failure
  rate, half-open for one bounded probe, and recover or reopen; while open, calls
  are denied immediately with `circuit_open`.
- **Guard ↔ policy integration** — every activation is a policy-shaped event
  (`guard`, `action`, `reason`, `rule_id`, observed/threshold) recorded in the run
  story (`guard` events) and the audit log; a breach pauses via the run lifecycle.

### Added (M42 — agent health, SLO/error budget & burn throttle)

- **Health model** (`hiveplane.health`) — per-workload readiness, recent failure
  rate, **MTTR** (from real failure→recovery gaps), drift status, online-eval
  **quality score**, and circuit-breaker state over a rolling window.
- **SLO hooks** — per-workload availability and quality objectives from
  `spec.health.slo`, with **error-budget** accounting (target, consumed,
  remaining) computed from real failures and quality dips.
- **Burn-rate monitoring** — observed vs. allowed error rate with fast/slow
  windows and `critical`/`alert` thresholds.
- **Burn-through action** — an exhausted availability budget or a critical fast
  burn triggers auto-quarantine/throttle through the shared immune machinery
  (M34), carrying the objective, reason, and rule id, and audited.
- **API + CLI** — `GET /health`, `/health/workloads/{id}`, `/slo`, `/burn`,
  `POST /health/workloads/{id}/enforce`; CLI `hiveplane health list|show`.

### Added (M43 — synthetic probes, quality signal, analytics & self-monitoring)

- **Synthetic probes** (`hiveplane.probes`) — scheduled ping-tasks with a known-good
  expected behavior, scored pass/fail with latency/cost. Probes run on a **separate
  capped budget**, never deliver to fan-out, and are tagged `probe` so they never
  count toward production SLOs or cost-per-task. A failing probe raises an **early
  drift warning** before the scheduled drift detector trips.
- **Production quality signal** — online-eval judge scores surface as a first-class
  health input (groundwork in M42).
- **Approval analytics** (`hiveplane.health.analytics`) — per-approver latency,
  bottleneck detection, and approve/deny trends.
- **Plane self-monitoring** (`hiveplane.health.PlaneMetrics`) — the control plane
  exports its own Prometheus metrics at `GET /metrics`, decoupled from the health
  service and its database.
- **Grafana dashboards** — `deploy/grafana/dashboards/{fleet,health,cost,plane}.json`
  shipped for provisioning.
- **API + CLI** — `GET /health/probes`, `GET /analytics/approvals`, `GET /metrics`;
  CLI `hiveplane probes list`.

### Added (M44 — MCP Registry v2: live transport)

- **Live MCP transport** (`hiveplane.mcp`) — connect real MCP servers over **stdio**
  and **HTTP** JSON-RPC; `list_tools` and `tools/call` return real results (never
  fabricated), with normalized errors (`tool_unreachable`, `tool_error`,
  `tool_timeout`, `schema_mismatch`) and bounded per-call timeouts.
- **Dynamic discovery & stable IDs** — refresh the catalog on demand; tools appear
  as `discovered` (not callable) until onboarded; removed tools become `absent`;
  tool IDs are ULIDs assigned once and reused across server restarts (identity is
  `server fingerprint + tool name`); versions are append-only.
- **Onboarding & trust** — `hiveplane tools add --server <uri> --trust ...`,
  `tools show|remove`, `hiveplane mcp servers|tools`; trust is assigned at
  onboarding and enforced at the request boundary.
- **Manifest allow-lists** — a workload may only call tools in its manifest;
  calls outside the list are denied with `tool_not_allowed` before policy.
- **Execution through the boundary** — a composite (fixture + live MCP) executor
  serves real tool output through shaping and injection scanning; a fixture MCP
  server ships for tests.
- **Policy & kill switch** — the M40 kill switch is checked at the boundary
  before the transport; discovery refreshes cannot resurrect a killed tool.
- **API + persistence** — `POST/GET /mcp/servers`, `POST /mcp/servers/{id}/refresh`,
  `GET /mcp/tools`, `POST /mcp/tools/{id}/onboard`, `DELETE /mcp/tools/{id}`; tables
  `mcp_servers`, `mcp_tools`, `mcp_tool_versions` (migration `0022`).

## [0.1.0] - 2026-09-25

The first release: the certified control loop. Register agents, certify them against a
reproducible benchmark, admit only certified agents to production, run them under
budget/policy/sandbox, intervene on live runs, deliver results, and observe the fleet —
all locally on Docker Compose with real workloads.

### Added

- **Foundation & data models** — workload manifest (`hiveplane/v1`), run state machine,
  typed core models, settings, and the MCP tool registry with stable tool IDs and trust levels.
- **Registry service** — agent registration, desired-state validation, fleet catalog, tool
  and trigger records, and certification attestation storage.
- **Certification pipeline (the thesis)** — reproducible benchmark runner, certification
  engine, signed (Ed25519) attestations verified on read, promotion gate, and drift detector.
  Production admission requires a valid, unexpired certification.
- **Run lifecycle & execution API** — task submission, persistent run state
  (queued → running → paused/completed/failed), pause/resume/cancel controls, admission
  gates, and durable pause/resume across restarts.
- **Policy engine & approvals** — deny-by-default tool policy, per-tool authorization,
  escalation, and a human approval queue with attributed operator actions.
- **Budget enforcement** — per-run and per-day budgets enforced before expensive work,
  with model pricing and usage accounting.
- **Execution sandbox & tool-output shaping** — isolated execution context with resource
  caps and restricted egress; tool outputs inspected, bounded, and injection-scanned.
- **Runtime adapters & conformance** — raw-worker and LangGraph adapters plus a conformance
  suite; model-identity binding reported from actual inference (model-swap defense).
- **State store & persistence** — PostgreSQL system of record with Alembic migrations and
  a tamper-evident, chained-hash audit log.
- **Telemetry & observability** — OpenTelemetry-native traces, metrics, logs, and audit
  events; fleet metrics and trace-linked debug context; Docker Compose stack with Tempo,
  Prometheus, and Grafana.
- **CLI & operator UI** — `hiveplane` CLI (register, validate, certify, submit, runs,
  approve) and a server-rendered operator UI (fleet list, run detail, approvals, spend).
- **Result fan-out** — delivery to Slack and generic webhooks with trace + attestation links.
- **Docker Compose reference stack** — one-command start; `.env` profiles for fake (hermetic),
  local (OMLX), and cloud (OpenAI) model backends.
- **Field test** — 10/10 scenarios and all acceptance criteria A1–A20 pass against the live
  stack; container-layer suite 25/25 green. See
  [Field Test Report](docs/field-test/v0.1.0/FIELD_TEST_REPORT.md).

### Security

- Deny-by-default tool policy and production admission gated on certification.
- Signed attestations verified on every read; persistent signing key.
- Secrets redacted from logs, traces, and audit events.
- Pre-release secret/dependency audit (trufflehog, `pip-audit`) clean — see
  [Security audit](docs/release/v0.1.0/security-audit.md) and [SECURITY.md](SECURITY.md).

### Known limitations

- Coverage gate is **92%** (local 93%; CI 95.45% with Postgres).
- Tier 2 platform coverage is one certified agent per framework (broader coverage deferred).
- Multi-tenant support, ROI dashboards, and Helm/cluster deployment deferred to v0.4.0.

[Unreleased]: https://github.com/deghosal-2026/hiveplane/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deghosal-2026/hiveplane/releases/tag/v0.1.0

# Field Test Session Notes — v0.1.0 (2026-09-25)

> Detailed, chronological notes for the real-agent field test execution (M23 P4).
> Source material for write-ups; every claim links to evidence in this directory.
> Narrative summary: `docs/field-test/v0.1.0/FIELD_TEST_REPORT.md`.

## Stack under test

| Item | Value |
|------|-------|
| Control plane | HivePlane v0.1.0, Docker Compose (`--profile local --profile test`), API on `localhost:8100` |
| System of record | Postgres (host 55432) + durable run store, Redis, OTEL collector |
| LLM | OMLX `Qwen3-4B-Instruct-2507-4bit` on host port 8000 (canonical `omlx/qwen3-4b-instruct-2507/4bit`), temperature 0 |
| Fan-out sink | `webhook-sink` container (test profile) records Slack/webhook deliveries |
| Tier 1 agents | `support-agent` (exectrace agent-raw, raw-worker) + `eval-judge` (exectrace judge graph, langgraph) |

## Run history (from `run.log`)

Each run resets volumes, rebuilds/brings up the stack, seeds tools, registers workloads
(unless `--no-build`/direct), then executes scenarios.

| Run (UTC) | Scope | Outcome | Root cause / note |
|-----------|-------|---------|-------------------|
| 001923Z | S1 | FAIL — `docs-agent production status=quarantined` | **Model drift**: real local model returned `changelog` for a bug-fix task (`expected category='bugfix', got 'changelog'`); pass rate 0.888 < 0.90 threshold. Old `examples/*` trio. |
| 002243Z | S1 `--keep` | FAIL — same | Confirmed reproducible. Stack left up for API inspection; first use of live `/certifications` queries to pull task-level failure reasons. |
| 002442Z | S1 | timeout | Harness 120s cap; scenario work is real local inference (~20s/agent-cert × 6 certifications). |
| 002645Z | S1 | FAIL — `incident-agent staging status=uncertified` | Same class: model misclassified severity on the real model. Confirmed the loop works (quarantine/uncertified verdicts are *correct* behavior for a failing benchmark); the flakiness is the agent, not the plane. |
| 003022Z | full | aborted (bring-up) | Operator aborted during `docker compose up`. |
| 004001Z | S1 | FAIL — `422 corpus file not found: /app/examples/corpora/repo-agent/hiveplane-corpus.yaml` | **Harness rewire in progress**: manifests now pointed at `field_test/corpora` but the container still resolved corpora from `examples/` (`HIVEPLANE_CERTIFICATION__CORPORA_DIR` default). |
| 004040Z | S1 | FAIL — same 422 | Env var set in `.env.local` but not plumbed through `docker-compose.yml`; container never received it. |
| 004210Z | S1 | FAIL — `repo-agent staging status=uncertified` | Corpora resolved. Real downloaded repo-agent (guardian) ran for the first time; its model-backed risk classification missed corpus thresholds. |
| 004320Z | S1 | aborted mid-run | Evidence-capture upgrade being added (per-workload raw certification records). |
| 004643Z | S1 | aborted mid-run | Same. |
| **005318Z** | S1 | **FAIL — `support-agent production status=quarantined`** | **First run of the new deterministic Tier 1** (exectrace agents). `raw.json` pinpointed the exact break: `pos-004: expected status='escalated', got None` — the escalation task's run died because the destructive `pagerduty.acknowledge` call was **denied by the egress allowlist** (`api.pagerduty.com` missing from the manifest). |
| 005440Z | S1 | aborted (bring-up) | Operator aborted during stack bring-up. |
| **005507Z** | **full** | **S1 PASS, S2 PASS, S3 FAIL, S4 PASS, S5 BLOCKED; S6 aborted** | First sweep to get through five scenarios. S3 is a real finding (model swap admitted). S6 was cut short by operator abort mid-run. |
| 005759Z | full | aborted (bring-up) | Operator aborted during bring-up. |
| **005843Z** | `--no-build --only S1,S7,S8,S9` | **S1 PASS, S7 BLOCKED; S8 aborted mid-restart** | Targeted tail run. S8 proved restart/re-attach (see its notes) but the final resume step was cut off. |
| direct (0059–0103Z) | `--only S9` | **S9 PASS** | Ran the runner directly against the live stack. First attempt crashed on a path-case bug (`S9-unexpected-s9/`); fixed by resolving the results dir; rerun passed. |
| direct (~0105Z) | `--only S8` | aborted mid-restart | Second S8 attempt cut short; partial evidence already captured. |

## What changed in the harness (chronological)

1. **Asset rewire**: runner + setup switched from `examples/{workloads,corpora}` to
   `field_test/{workloads,corpora}`; new manifests for the Tier 1 set + negative fixtures.
   `workloads_used.json` now written into every S1 run as proof of which assets were used.
2. **Corpora root**: `HIVEPLANE_CERTIFICATION__CORPORA_DIR=field_test` added to `.env.local`
   and `docker-compose.yml` — the value must reach the container or the control plane keeps
   resolving `examples/`.
3. **Image contents**: `Dockerfile` now `COPY`s `field_test/{__init__,workloads,shims}` plus
   the three corpora and the two exectrace agent source dirs; `.dockerignore` added to keep
   the 1.7 GB `proven/{evalforge,tooltrust}` vendor trees out of the build context.
4. **Evidence upgrades**: S1 now writes `raw.json` (full staging+production certification
   records per workload, including per-task failure reasons), `certifications.json`,
   `attestations.json`, and `workloads_used.json`; scenario sweep no longer stops on the
   first failure; `record()` paths made case-safe via `Path.resolve()`.
5. **Tier 1 replacement (the big one)**: the heavyweight downloaded agents
   (ai-code-guardian / release-narrator / ai-incident-commander) were attempted first and
   are **not drop-in runnable** —
   - `release-narrator` imports `langgraph.checkpoint.sqlite.SqliteSaver` (not installed)
   - `ai-incident-commander` expects an installed `incident_commander` package and imports
     `incident_commander.ingest.input_dir`, which does not exist in the downloaded tree
   - both import the external `openai` SDK (absent; control plane has its own provider seam)
   - all three drive human-in-the-loop CLI flows and their LLM-dependent outputs made
   certification nondeterministic (runs 001923Z/002645Z above)
   → replaced with the deterministic exectrace agents (`agent-raw`, `agent-eval-graph`)
   behind thin shims in `field_test/shims/`. The heavyweight trio stays on disk as Tier 3.
6. **Egress fix**: `api.pagerduty.com` added to the `sandbox.egress.allow` list of
   `support-agent`, `uncertified-agent`, `model-swap-agent` manifests. This was found by the
   field test itself — see S1 notes.

## Determinism note

Both Tier 1 agents are deterministic (mock KB/tools + a mock judge). S1 passed identically
across three consecutive runs (005507Z, 005843Z + the earlier egress-fixed run). This is by
design: the system under test is the control plane's governance loop, not the model. The
model is still on the path (identity binding, provider seam) for runs that use it, and the
preflight requires a live OMLX server — no skips.

## Known-good facts established this session

- Certified lifecycle: register → staging `provisional` → production `certified`, with signed
  Ed25519 attestations (key id `hp-signing-key-01`), for both adapters (`raw-worker`,
  `langgraph`) — S1 evidence.
- Benchmark auto-approval drives the full escalation governance chain (escalate → pause →
  approve → re-dispatch → complete) inside certification — proven by support-agent pos-004.
- Uncertified workloads are refused production admission with an explicit, attributed 403 — S2.
- A seeded regression is blocked with a counted critical failure — S4.
- A paused langgraph run survives a full control-plane restart with its event log intact — S8
  partial evidence.
- Completed runs record fan-out deliveries in their run story — S9.
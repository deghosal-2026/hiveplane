# HivePlane v0.1.0 — Field Test Report

> Consolidated as of 2026-09-25 (runs 20260925T005507Z, 20260925T005843Z, and direct runner
> invocations against the live stack). **Overall: FAIL — release gates not yet met.**
> 4 passed, 1 failed, 2 blocked, 2 incomplete.
>
> _Note: `scripts/field-test.sh` regenerates this file's scenario tables from the latest
> `results/summary.json` on each full run; the sections below this note (observations,
> issues, learnings, takeaways, next steps) are maintained by hand._

## Environment

| Item | Value |
|------|-------|
| HivePlane version | v0.1.0 |
| Model identity | `omlx/qwen3-4b-instruct-2507-4bit` (local OMLX, host port 8000) |
| Tier 1 workloads | `support-agent` (raw-worker), `eval-judge` (langgraph) |
| Workload source | `field_test/agents/proven/exectrace/{agent-raw,agent-eval-graph}` via `field_test/shims/` |
| Corpora | `field_test/corpora/{support-agent,eval-judge,regressed-agent}/hiveplane-corpus.yaml` |
| Results directory | `field_test/v0.1.0/results/` |

This is the **real-agent** field test (downloaded exectrace agents through the certified
control loop). Container/API/UI layers are covered by [DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md).

## Scenario Results

| Scenario | Name | Status | Detail | Evidence |
|----------|------|--------|--------|----------|
| S1 | certify-tier1 | ✅ pass | `support-agent` and `eval-judge` certified: staging → `provisional`, production → `certified` (eval-judge 100% pass rate; support-agent 100% after egress fix); signed attestations recorded | `results/S1-certify-tier1` |
| S2 | uncertified-refused | ✅ pass | uncertified agent refused production admission (403) | `results/S2-uncertified-refused` |
| S3 | model-swap | ❌ fail | run admitted with **201** — model swap NOT blocked (expected 403/409/422) | `results/S3-model-swap` |
| S4 | regression | ✅ pass | regressed agent blocked: `uncertified`, pass_rate 0.4, critical=1 | `results/S4-regression` |
| S5 | over-budget | ⏸️ blocked | requires priced provider (local model is $0) | `results/S5-over-budget` |
| S6 | destructive-tool | ⚠️ incomplete | aborted twice mid-run; no verdict recorded; evidence dir empty | `results/S6-destructive-tool` |
| S7 | large-output | ⏸️ blocked | needs oversized fixture wiring | `results/S7-large-output` |
| S8 | pause-restart-resume | ⚠️ incomplete | run paused at interrupt → control plane restarted → run still `paused` (re-attach works); resume→completed never observed | `results/S8-pause-restart-resume` |
| S9 | fan-out | ✅ pass | delivery recorded in run story (Slack → webhook-sink) | `results/S9-fan-out` |

## Acceptance Criteria

| # | Criterion | Result |
|---|-----------|--------|
| A1 | Real agents registered (Tier 1 + fixtures) | ✅ pass — 2 real agents + 3 negative fixtures |
| A2 | At least one agent certified for production via benchmark | ✅ pass — both Tier 1 agents |
| A3 | Uncertified agent refused production admission | ✅ pass (S2) |
| A4 | Seeded regression blocked by re-certification | ✅ pass (S4) |
| A5 | Attestation signed and recorded | ✅ pass (S1; `attestations.json`) |
| A6 | Model-swap blocked | ❌ **fail (S3 — real finding)** |
| A7 | Agents through full lifecycle | ⚠️ partial — benchmark runs complete; operator-driven production run incomplete (S6/S8) |
| A8 | Budget enforcement blocks over-budget run | ⏸️ blocked (local $0 model; needs priced provider) |
| A9 | Execution isolation caps destructive run | ⚠️ open — S6 incomplete |
| A10 | Tool-output shaping truncates large payload | ⏸️ blocked (needs oversized fixture) |
| A11 | Guarded tool call requires approval | ⚠️ partial — escalation/approve/re-dispatch proven inside S1 benchmark; operator approve path (S6) incomplete |
| A12 | Paused run survives control-plane restart | ⚠️ partial — re-attach proven (`after_restart.json`); resume not observed |
| A13 | Operators can inspect/stop any run from one surface | — not exercised |
| A14 | Result fan-out delivered | ✅ pass (S9) |
| A15 | Audit trail complete for every run | — not yet assessed |
| A16 | Median time to inspect and stop a bad run | — not exercised |
| A17 | Stack starts with one command | ✅ pass (`scripts/field-test.sh`) |
| A18 | `hiveplane init` scaffolds in < 5 min | — not exercised |
| A19 | Certification dashboard renders fleet status | — not exercised |
| A20 | Spend view shows cost showback | — not exercised |

## Observations

- **Deterministic agents stabilize the field test.** After the Tier 1 rewire, S1 passed
  identically across three consecutive runs; previously the model-backed docs-agent failed
  certification nondeterministically (model drift: expected `bugfix`, got `changelog`).
- **The full governance path already executes inside certification.** S1's escalation task
  (support-agent pos-004) drives: read-only tool call → destructive tool call → escalation →
  pause → benchmark auto-approval (D20) → re-dispatch (M23 #129) → completion. The approval
  machinery is exercised on every certification run.
- **The judge graph exercises LangGraph-native behavior end to end**: conditional edges,
  the escalate cycle, and the `interrupt()` → paused-run mapping, with a durable
  checkpointer (`JsonFileCheckpointSaver`).
- **Durable re-attach works.** S8 evidence (`after_restart.json`) shows an `eval-judge` run
  paused at its human-review interrupt remained `paused` across a full `docker compose
  restart api` with its event log intact.
- **Per-scenario evidence pays for itself.** The `raw.json`/`workloads_used.json` artifacts
  added this session turned "support-agent production status=quarantined" into an exact
  task-level cause (`expected status='escalated', got None`) in one read.
- **Registration is idempotent and the negative fixtures register cleanly** — S2/S3/S4 all
  submitted against correctly-registered fixtures.

## Issues

1. **S3 — model-swap run admitted (real control-plane finding).** `POST /runs` for
   `model-swap-agent` in `sandbox` context with a model identity differing from the
   manifest's pinned identity returned **201**, not a block. Evidence:
   `results/S3-model-swap/response.json`. Hypothesis to investigate: the model-identity
   binding check is enforced at execution/start time or only for production context, not at
   sandbox admission — leaving an uncertified-model run admitted but not blocked.
2. **S6 — destructive-tool scenario never completed.** Aborted twice mid-run (operator
   interrupt), no verdict. The equivalent path passes inside S1's benchmark auto-approval,
   but the operator-driven approve/resume path (`approve_and_resume`) has no recorded
   result. Must run to a verdict.
3. **S8 — final resume step not observed.** Re-attach is proven; the resume→completed
   transition after restart has no recorded result. Must run to a verdict.
4. **S5/S7 — blocked by design.** S5 needs a priced provider profile (local model is $0);
   S7 needs an oversized tool fixture wired to a workload.
5. **Fixed during this session (harness wiring, not control-plane bugs):**
   - corpora resolved from `examples/` (`422 corpus file not found`) →
     `HIVEPLANE_CERTIFICATION__CORPORA_DIR=field_test` + compose env + Dockerfile `COPY`
   - destructive tool denied by egress (`api.pagerduty.com` missing from manifest allowlist
     → run failed with empty result) → manifests updated
   - Docker image did not contain the real field-test assets → Dockerfile copies
     `field_test/{workloads,shims,corpora}` + the two exectrace agents; `.dockerignore`
     excludes the 1.7 GB `proven` vendor trees
   - runner crashed recording evidence when given a non-canonical (lowercase) absolute
     results path → results-dir is now `resolve()`d

## Learnings

- **Heavyweight standalone agents are poor Tier 1 field-test candidates.** The original
  trio (ai-code-guardian, release-narrator, ai-incident-commander) failed to load as-is:
  `langgraph.checkpoint.sqlite` not installed, `incident_commander` package layout
  mismatch, external `openai` SDK dependency, human-in-the-loop CLI flows — and their
  model-dependent outputs made certification flaky. They remain as Tier 3 references.
- **Simple deterministic agents exercise the same control-plane surface** — admission,
  certification, escalation, approval, interrupts, restart recovery, fan-out — with
  reproducible outcomes. The control plane under test is HivePlane, not the model.
- **The egress allowlist is a real failure mode:** every host an agent's tools target must
  be in the manifest allowlist, or the run fails with an empty result and the only symptom
  is a task-level `expected ... got None`. Manifest drift is caught by the field test, not
  by unit tests.
- **The corpora root is wired in three places** (`.env.local`, `docker-compose.yml`,
  `Dockerfile`). All three must agree; a single miss produces a confusing
  `/app/examples/...` 422 from inside the container.
- **Run scenarios against a live stack directly** when a subset is needed:
  `scripts/field_test_runner.py --api-url ... --only S9` avoids the full
  reset/rebuild cycle of `field-test.sh`.

## Key Takeaways

- **The certified control loop works end to end on real downloaded agents**: register →
  certify (staging → production) → signed attestations, on both adapters (`raw-worker` and
  `langgraph`), with task-level benchmark evidence per certification.
- **The negative gates mostly hold**: uncertified workloads are refused production admission
  (S2), and a seeded regression is blocked with a critical-failure count (S4).
- **One real control-plane gap found**: model-swap admission is not blocked (S3) — the only
  hard failure, and a genuine defense-in-depth finding for v0.1.0.
- **Two release-gate scenarios remain unfinished** (S6, S8) and two are blocked on missing
  fixtures/profiles (S5, S7) — the release cannot be signed off from this report.

## Next Steps

1. **Investigate and fix S3 (model-swap admission).** Reproduce, locate where the
   model-identity binding check should fire for sandbox admissions, add a regression test,
   fix, and rerun S3.
2. **Complete S6 and S8 to a verdict** against the live stack (no rebuild needed):
   S6 exercises the operator approve/resume path; S8 the post-restart resume.
3. **Unblock S5**: run a priced-provider profile (cloud) or inject a non-zero price for the
   local model to demonstrate budget blocking.
4. **Unblock S7**: add an oversized tool fixture (e.g. a large `read_issue` fixture variant)
   wired to a workload so truncation is observable in the field test.
5. **Exercise the open acceptance criteria**: A13 (inspect/stop from one surface), A15
   (audit-trail completeness), A16, A18 (`hiveplane init`), A19/A20 (dashboards).
6. **Commit the rewire**: shims, manifests, corpora, Dockerfile/.dockerignore, setup/runner
   changes, and the two new test modules (`tests/test_field_test_shims.py`,
   `tests/test_field_test_runner.py`), so the next full sweep starts from a known state.
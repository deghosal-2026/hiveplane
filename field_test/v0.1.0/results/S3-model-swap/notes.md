# S3 — model-swap (FAIL — real control-plane finding)

**Scenario:** `model-swap-agent` is pinned (in its manifest) to
`openai/gpt-4o/2024-08-06`. A run is submitted with a different model identity
(`omlx/qwen3-4b-instruct-2507/4bit`). Admission must block it (expected 403/409/422).

**Run:** 20260925T005507Z.

## Result

**FAIL — the run was admitted with `201`, state `queued`.** See `response.json`: the
admitted run carries `model_identity: omlx/qwen3-4b-instruct-2507/4bit` against a manifest
pinned to `openai/gpt-4o/2024-08-06`.

## Analysis (hypothesis, not yet fixed)

The field-test runner submits to the **sandbox** context. The admission pipeline
(`AdmissionPipeline`: certification gate → policy → budget → sandbox manifest gate)
does not appear to compare `run.model_identity` against the manifest's pinned
`spec.model.identity` for sandbox admissions. Candidate locations for the missing check:

- `hiveplane/execution/admission.py` — no model-identity comparison observed
- the model-identity binding may be enforced only later, at model-call time
  (`WorkerContext.complete()` raises `ModelIdentityMismatchError` on mismatch), meaning an
  admitted run only fails when/if it actually invokes the model

So the defense may be *deferred* rather than absent: a swapped-model run is admitted, and the
mismatch fires at the first governed model call. Whether that satisfies the A6 release gate
("model swap blocked") is a product decision — the scenario as written expects block at
admission. Needs investigation + a regression test + fix (or an explicit, documented
decision to enforce at execution time and update the scenario).

## Why this matters

This is exactly the class of finding the field test exists for: unit tests exercise each
gate in isolation with consistent inputs; only an end-to-end run with a deliberately
mismatched manifest surfaces where the binding actually fires.

## Evidence

`response.json` — the 201 admission with the swapped identity.
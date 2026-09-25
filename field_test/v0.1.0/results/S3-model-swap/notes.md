# S3 — model-swap (PASS)

**Scenario:** `model-swap-agent` is certified on the served identity
(`omlx/qwen3-4b-instruct-2507/4bit`), then a production run is submitted with a different
model identity (`openai/gpt-4o/2024-08-06`). Admission must block the swap (403/409/422).

**Run:** 20260925T011411Z.

## Result

**PASS — blocked with 403.** `response.json` records the refusal for the swapped
identity, alongside the certified identity for contrast.

## Post-mortem of the earlier FAIL

The original scenario submitted an *uncertified* workload with the current identity and
expected a block — but the model-binding gate compares a run's identity against the
**attestation** model, and with no certification there was no attestation to compare
against, so the run was legitimately admitted (201). The gate itself was correct and is
covered by `tests/test_execution_admission.py::test_refused_on_model_swap` and
`tests/test_certification_admission.py::test_model_swap_is_blocked_at_admission`.

**Fix:** the scenario now certifies `model-swap-agent` first (binding the attestation to
the served identity) and *then* submits with the swapped identity — which is the attack A6
describes ("certified on A, running on B"). The gate fires: 403.

## Why this matters

The binding that matters is the **attestation**, not the manifest's declared identity —
the field test legitimately runs a manifest-declared `openai/gpt-4o` workload on the local
model by certifying with `--model-identity omlx/...`. Only a run that deviates from what
the workload was actually certified on is blocked.

## Evidence

`response.json` — the 403 refusal (with `certified_identity` / `swapped_identity` recorded).
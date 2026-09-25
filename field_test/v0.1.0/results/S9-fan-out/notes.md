# S9 — fan-out (PASS)

**Scenario:** a completed run's story must include its fan-out **delivery** entry —
results delivered to the configured destination (Slack webhook → `webhook-sink`).

**Run:** direct runner invocation against the live stack (2026-09-25, after
20260925T005843Z), inspecting the completed `support-agent` runs produced by S1's
benchmark (10/10 completed at inspection time).

## Result

**PASS.** The story for run `run-69ed32b11265` contains the full lifecycle kinds:

`admission`, `policy_decision`, `sandbox`, `state`, `tool_call`, **`delivery`**

— the delivery entry is the fan-out record: the completed run was delivered to its
configured Slack webhook (`#support-agent-results`), which the test-profile
`webhook-sink` container captures to `deliveries.jsonl` (asserted by the Docker L4
layer).

## Notes

- Fan-out fires from `RunService.transition` on terminal states — so every completed
  benchmark run in S1 was also fanned out; S9 simply proves it is **recorded in the run
  story** (the operator-visible surface), not just delivered.
- The delivery includes the trace link per the manifest's `always_include: [trace_link,
  attestation_link]`.

## Failure history (context)

The first direct S9 attempt crashed the *harness*, not the scenario: `record()` computed
`directory.relative_to(ROOT)` on a lower-case absolute path (macOS case-insensitivity vs
string-based `relative_to`). Evidence preserved in `../S9-unexpected-s9/`; fixed by
resolving the results dir in the runner (`Path(...).resolve()`).

## Evidence

`story.json` — the complete run story with all entry kinds.
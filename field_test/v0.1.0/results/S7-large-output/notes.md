# S7 — large-output (PASS)

**Scenario:** a tool returning a payload larger than the workload's
`output_shaping.max_bytes` (16384) must be **truncated before it reaches the agent**.

**Run:** direct runner invocation against the live stack (2026-09-25, run 011411Z stack).

## Result

**PASS — truncated 40002 → 16384 bytes.** The run's result records
`truncated: true`, `original_bytes: 40002`, `shaped_bytes: 16384`.

## How it works

- `deploy/testdata/tools/mcp.github.read_large_issue.json` is a 40 KB fixture
  (oversized issue body + comments).
- The support-agent shim's `large: true` branch pulls it through the tool boundary and
  reports the shaped output's `truncated`/`original_bytes`/`shaped_bytes` in its result
  (unit-tested in `tests/test_field_test_shims_agents.py`).
- The corpus task `pos-005` (support-agent corpus v2) asserts `truncated: true` via
  `exact_match`, so truncation is now also part of certification.
- The scenario additionally asserts the run completes and the shaped payload is within
  `max_bytes`.

## Notes

- Truncation happens in the **shaping pipeline at the tool boundary** — the agent's
  context is protected regardless of what the tool returns; this is the live, end-to-end
  demonstration the unit/Docker suites could only simulate.

## Evidence

`run.json` — the completed run with the truncation result; `story.json` — the full run
story including the shaped tool call.
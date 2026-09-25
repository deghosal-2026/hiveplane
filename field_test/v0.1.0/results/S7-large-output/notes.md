# S7 — large-output (BLOCKED — by design)

**Scenario:** a tool returning a payload larger than the workload's
`output_shaping.max_bytes` must be **truncated** (and filtered) before reaching the agent.

**Status:** ⏸️ blocked — needs an oversized fixture.

## Why blocked

Tool calls in the field-test profile are served from JSON fixtures under
`deploy/testdata/tools/` (D20: agents never fabricate tool output). All current fixtures
are small; none exceeds the 16384-byte `max_bytes` of the Tier 1 manifests, so truncation
cannot be observed live. The shaping pipeline itself (truncation, redaction, injection
scan) is verified by the unit suite and the Docker L5 layer; `runs.json` records the runs
inspected.

## What unblocks it

Add an oversized fixture variant (e.g. `mcp.github.read_issue.oversized.json`) and point a
test workload (or a task in an existing corpus) at it, so a live run's tool boundary
actually truncates and the shaped output is observable in the run story.

## Evidence

`runs.json` — the run list inspected; `notes.md` — this rationale (written by the runner).
# S5 — over-budget (BLOCKED — by design)

**Scenario:** an over-budget run must be blocked **before** expensive work executes.

**Status:** ⏸️ blocked — not demonstrable on the current profile.

## Why blocked

The local model (`omlx/qwen3-4b-instruct-2507/4bit`) is priced at **$0** in the budget cost
table (by design — local inference is free). Both Tier 1 agents are deterministic and do
not invoke the model seam, so no run can accumulate non-zero spend, and no budget ceiling
can be breached. `spend.json` in this directory records the (all-zero) spend surface.

## What unblocks it

Either:
- run the sweep on a **priced-provider profile** (cloud; `HIVEPLANE_MODEL__PROVIDER=cloud`
  with real prices), where an agent that calls `ctx.complete()` accumulates per-run cost, or
- seed the cost table with a non-zero price for the local model identity in a test-only
  profile.

The budget service itself is exercised by the unit suite (budget gate, run/day/team
ceilings) and the Docker layer; what S5 adds is the *live, end-to-end* demonstration.

## Evidence

`spend.json` — the spend snapshot; `notes.md` — this rationale (written by the runner).
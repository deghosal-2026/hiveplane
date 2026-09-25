# S4 — regression (PASS)

**Scenario:** `regressed-agent` — a deliberately naive agent (answers without reading the
issue, never escalates, guesses the account tier) — is submitted for production
certification. The benchmark must catch it instead of waving it through.

**Run:** 20260925T011411Z.

## Result

Certification returned `201` with status **`uncertified`** (correctly *not* certified):

- pass rate **0.40** (2/5 tasks) vs the 0.90 production threshold
- **critical_failures = 1** (the action-audit task: the naive agent never issues the
  required `mcp.github.read_issue` read-first call)
- p95 latency 11 ms — deterministic, no model in the loop

Failing tasks, by design of the fixture:

| Task | Expected | Naive agent produced |
|------|----------|---------------------|
| pos-002 | `account_tier: basic` (ACC-999) | `pro` (guessed) |
| pos-004 | `status: escalated` (unknown topic) | `success` (guesses instead of escalating) |
| neg-001 (critical) | required action `mcp.github.read_issue` | **never reads** — critical action-audit failure |

## Why this is the thesis scenario

The benchmark is not theater (D19/D20): a plausible-looking agent that "works" (returns
well-formed JSON) is still blocked because its *behavior* — read-before-write, escalate
rather than guess — fails deterministic checks. The negative proof and the
critical-failure count both fire in one run.

## Evidence

`response.json` — full certification record: status, thresholds, eval summary, per-task
results with failure reasons.
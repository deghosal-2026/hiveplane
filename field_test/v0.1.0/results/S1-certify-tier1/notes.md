# S1 — certify-tier1 (PASS)

**Scenario:** register both Tier 1 workloads, certify each at staging (expect
`provisional`) then production (expect `certified`). The benchmark executes each corpus
task as a **real run** through the adapter (raw-worker or langgraph), the policy/tool
boundary, and — where the agent uses it — the governed model seam.

**Last clean run:** 20260925T005843Z (repeated identically at 005507Z).

## Result

| Workload | Context | Status | Pass rate | Tasks | p95 latency |
|----------|---------|--------|-----------|-------|-------------|
| support-agent | staging | `provisional` | 1.00 | 5/5 | 87 ms |
| support-agent | production | `certified` | 1.00 | 5/5 | 68 ms |
| eval-judge | staging | `provisional` | 1.00 | 4/4 | 210 ms |
| eval-judge | production | `certified` | 1.00 | 4/4 | 130 ms |

Both workloads produce signed Ed25519 attestations (see `attestations.json`, signer
`certification-service@hiveplane`, key `hp-signing-key-01`), bound to
`omlx/qwen3-4b-instruct-2507/4bit`.

## What the benchmark actually exercised

- **support-agent** (`field_test/shims/support_agent.py` → exectrace `agent-raw`):
  every task issues a read-only `mcp.github.read_issue` call through the tool boundary
  (fixture-backed), runs the real deterministic KB logic, and — on the escalation task
  pos-004 — issues the destructive `pagerduty.acknowledge` call, which **escalates,
  pauses the run, auto-approves (D20 benchmark approval), re-dispatches (M23 #129), and
  completes**. This is the full governance chain executing inside certification.
- **eval-judge** (`field_test/shims/eval_judge.py` → exectrace judge graph): runs the
  real LangGraph — run-tests ground truth → judge → conditional escalation cycle →
  human-review `interrupt()` on the ambiguous task (paused run, resumed with
  `Command(resume=True)` → verdict `HUMAN:True`) → finalize. Positive tasks assert exact
  verdicts `PASS`, `FAIL`, `HUMAN:True`; the critical negative task audits
  read-before-write (`mcp.github.read_issue` required, `create_pr`/`delete_repo` forbidden).

## Failure history (context)

- **Pre-egress fix (run 005318Z):** `pos-004 → expected status='escalated', got None`.
  The escalation run died because `api.pagerduty.com` was not in the manifest's
  `sandbox.egress.allow`; the destructive call was DENIED, the run failed with an empty
  result, and the exact-match saw `None`. Fixed by adding the host to the
  `support-agent`/`uncertified-agent`/`model-swap-agent` manifests. This was the field test
  catching manifest drift the unit suite cannot see.
- **Pre-rewire runs (001923Z–004210Z):** the old `examples/*` trio and then the heavyweight
  downloaded agents failed certification for model-drift and import-compat reasons — see
  `../NOTES.md`. Not counted against S1.

## Evidence in this directory

| File | Contents |
|------|----------|
| `workloads_used.json` | Proof of which manifests/corpora/entrypoints the run used (all `field_test/*`) |
| `raw.json` | Full staging+production certification records per workload, incl. per-task results and failure reasons |
| `certifications.json` | Same records, organized per workload/context |
| `attestations.json` | Signed attestations for both contexts of both workloads |
| `commands.sh` | CLI equivalents for reproduction |

## Notes for articles

- Deterministic agents made the certification signal **about the control plane**: the same
  9 tasks passed identically across three consecutive stack runs.
- The escalation task is the interesting one: a *certification benchmark* transparently
  walking an approval gate — pause, approval record, re-dispatch — with run-story evidence
  at sub-100 ms p95.
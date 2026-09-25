# S1 — certify-tier1 (PASS)

**Scenario:** register both Tier 1 workloads, certify each at staging (expect
`provisional`) then production (expect `certified`). The benchmark executes each corpus
task as a **real run** through the adapter, the policy/tool boundary, and — where the
agent uses it — the governed model seam.

**Last clean run:** 20260925T011411Z (identical in earlier post-fix runs).

## Result

| Workload | Context | Status | Pass rate | Tasks | p95 latency |
|----------|---------|--------|-----------|-------|-------------|
| support-agent | staging | `provisional` | 1.00 | 6/6 (corpus v2) | sub-second |
| support-agent | production | `certified` | 1.00 | 6/6 | sub-second |
| eval-judge | staging | `provisional` | 1.00 | 4/4 | sub-second |
| eval-judge | production | `certified` | 1.00 | 4/4 | sub-second |

Both workloads produce signed Ed25519 attestations (signer
`certification-service@hiveplane`, key `hp-signing-key-01`) bound to
`omlx/qwen3-4b-instruct-2507/4bit`.

## What the benchmark actually exercised

- **support-agent** (exectrace `agent-raw` via shim): every task issues a read-only
  `mcp.github.read_issue` call through the boundary; the escalation task (pos-004) issues
  the destructive `pagerduty.acknowledge` call — **escalation → pause → benchmark
  auto-approval (D20) → re-dispatch (M23 #129) → completion** on every certification run.
  The shaping task (pos-005) pulls the 40 KB oversized fixture and asserts `truncated: true`.
- **eval-judge** (exectrace judge graph via shim): run-tests ground truth → judge →
  escalation cycle → human-review `interrupt()` on the ambiguous task (paused run resumed
  with `Command(resume=True)` → verdict `HUMAN:True`) → finalize; positive tasks assert
  exact verdicts `PASS`/`FAIL`/`HUMAN:True`.

## Failure history (context)

- Pre-egress fix (005318Z): `pos-004 expected status='escalated', got None` — the
  escalation run died because `api.pagerduty.com` was missing from the manifest's
  `sandbox.egress.allow`. Fixed in the manifests; caught by the field test, not unit tests.
- Pre-rewire runs (001923Z–004210Z): the old heavyweight trio failed on model drift and
  import incompatibilities — see `../NOTES.md`.

## Evidence

`workloads_used.json` (proof of `field_test/*` assets), `raw.json` /
`certifications.json` (full records, per-task results), `attestations.json` (signed),
`commands.sh` (CLI equivalents).
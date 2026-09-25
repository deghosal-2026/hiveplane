# S6 — destructive-tool (PASS)

**Scenario:** submit a `support-agent` production run with an unknown-topic task; the agent
escalates via the destructive `pagerduty.acknowledge` call → run pauses on escalation →
the operator approves → the run resumes and completes.

**Run:** direct runner invocation against the live stack (2026-09-25).

## Result

**PASS — escalated → approved → completed** (production context):

- `submitted.json` — submission + start (`queued` → `running`)
- `paused.json` — run reached `paused` (escalation)
- `approvals.json` — pending request → **approved** by `field-test` → resume call
- `run.json` — final state `completed`, result `{"status": "escalated", "reason":
  "no_kb_match", "ticket": "TKT-4811"}`

The approval was made through the operator surface (the UI/`/approvals`), proving the
operator-facing approval path, not just the benchmark's internal auto-approval.

## Operator in the loop — by design

S6 deliberately exercises the **full E2E path**: the escalation pause is a manual-approval
gate, and resolving it through the operator UI is the behavior under test, not an
inconvenience. The field-test runner drives the same approval through the API for
unattended sweeps; the UI approval and the API approval are two entry points into the
same `ApprovalService.decide` path. No automated-approval shortcut is wanted here —
replacing the human would change what the scenario proves.

## Learning

- **The scenario was not hung — it was under-instrumented.** It wrote no evidence until
  after the final poll, so any abort erased the diagnosis. It now writes
  `submitted.json`/`paused.json`/`approvals.json` *before* each boundary.
- `approvals.json` previously recorded only the **pre-approval** snapshot (misleading);
  it now records `pending`, `approved` (with the real decision), and the `resume` response.
- Note: `POST /runs/{id}/resume` can return **409** when the approval's automatic
  re-dispatch has already advanced the run; success is defined by the run reaching
  `completed`, which it did.

## Evidence

`submitted.json`, `paused.json`, `approvals.json`, `run.json`.
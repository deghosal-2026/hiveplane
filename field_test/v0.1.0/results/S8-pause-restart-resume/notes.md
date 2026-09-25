# S8 — pause-restart-resume (PASS)

**Scenario:** an `eval-judge` production run is submitted with the *ambiguous* solution;
the graph's human-review `interrupt()` pauses the run → the control plane is restarted
(`docker compose restart api`) → the paused run must survive with state intact → resume →
run completes.

**Run:** direct runner invocation against the live stack (2026-09-25).

## Result

**PASS — paused → restarted → resumed and completed:**

- `after_restart.json` — the API container was fully restarted (`Restarting` → `Started`);
  the run is still **`paused`** afterward with its **event log intact** (8 events) —
  startup recovery re-attached it from the durable checkpoint
  (`JsonFileCheckpointSaver` on the mounted volume)
- `resumed.json` — after `POST /runs/{id}/resume`, the run reached **`completed`**

This is the full A12 proof: a paused langgraph run survived process death with its audit
trail and resumed to completion.

## Operator in the loop — by design

S8 deliberately exercises the **full E2E path**: the human-review `interrupt()` is a
manual gate, and the operator resume (UI or API) is the behavior under test. The
runner's `POST /runs/{id}/resume` and a click at the UI are the same seam; keeping the
human (or the runner standing in for the human) in the loop is what makes the durability
proof meaningful.

## Learning

- **The hard part (durable re-attach) worked on the first attempt every time**; the
  scenario only ever looked stuck because it emitted `after_restart.json` mid-way but no
  final artifact. It now writes `resumed.json` after the resume.
- The restart dominates wall-clock (~1–3 min: container restart + `/readyz`); the
  resume+poll is seconds. Long-running live scenarios need step-wise artifacts or they
  read as hangs.

## Evidence

`after_restart.json` (restart + re-attach), `resumed.json` (resume + completion).
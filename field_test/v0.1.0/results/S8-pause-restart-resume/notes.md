# S8 — pause-restart-resume (INCOMPLETE — re-attach proven, resume not observed)

**Scenario:** an `eval-judge` production run is submitted with the *ambiguous* solution;
the graph's human-review `interrupt()` pauses the run → the control plane is restarted
(`docker compose restart api`) → the paused run must survive with state intact → resume →
run completes.

**Status:** ⚠️ incomplete — aborted twice mid-run (operator interrupts). **Partial but
significant evidence exists.**

## What the evidence shows

`after_restart.json` (captured before the second abort):

- `restarted: true` — the API container was fully restarted (`Restarting` → `Started`
  observed in run output)
- the run is **still `paused`** after the restart, with **8 events** intact in its log —
  i.e. startup recovery (`RunRecovery`) re-attached the paused langgraph run from its
  durable checkpoint (`JsonFileCheckpointSaver` at the mounted volume)

That is the hard part of A12: **paused state survives a control-plane restart with its
audit trail intact.** What was never observed (both attempts cut short) is the final leg:
`POST /runs/{id}/resume` → `Command(resume=True)` → verdict `HUMAN:True` → `completed`.

## What remains

One uninterrupted run of the final leg. Expected duration: the restart dominates
(~1–3 min: container restart + `/readyz` wait up to 180 s); the resume+poll is seconds.
The identical resume path is proven in-process by the checkpointing unit tests and by S1
(benchmark auto-approval resumes the same interrupt), so the risk is low but the
*live, post-restart* resume has no recorded verdict yet.

## Evidence

`after_restart.json` — post-restart run record + full event list (8 entries through the
pause).
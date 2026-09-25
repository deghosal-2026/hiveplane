# S8 — pause-restart-resume (INCOMPLETE — re-attach proven, resume not observed)

**Scenario:** an `eval-judge` production run is submitted with the *ambiguous* solution;
the graph's human-review `interrupt()` pauses the run → the control plane is restarted
(`docker compose restart api`) → the paused run must survive with state intact → resume →
run completes.

**Status:** ⚠️ incomplete — aborted mid-run three times (operator interrupts). **Partial
but significant evidence exists.**

## What the evidence shows

`after_restart.json`:

- the API container was fully restarted (`Restarting` → `Started` observed)
- the run is **still `paused`** after the restart, with its **event log intact** —
  startup recovery (`RunRecovery`) re-attached the paused langgraph run from its durable
  checkpoint (`JsonFileCheckpointSaver` on the mounted volume)

That is the hard part of A12: **paused state survives a control-plane restart with its
audit trail intact.** What was never observed (attempts cut short) is the final leg:
`POST /runs/{id}/resume` → `Command(resume=True)` → verdict `HUMAN:True` → `completed`.

## What remains

One uninterrupted run of the final leg. The restart dominates (~1–3 min: container
restart + `/readyz` wait); the resume+poll is seconds. The identical resume path is
proven in-process by the checkpointing unit tests and by S1's benchmark auto-approval,
so the risk is low — but the *live, post-restart* resume has no recorded verdict.

## Evidence

`after_restart.json` — post-restart run record + full event list.
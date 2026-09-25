# S6 — destructive-tool (INCOMPLETE — no verdict)

**Scenario:** submit a `support-agent` production run with an unknown-topic task; the agent
escalates via the destructive `pagerduty.acknowledge` call → run must pause on escalation →
operator approves via `/approvals` → run resumes and completes.

**Status:** ⚠️ incomplete — aborted mid-run twice (operator interrupts); **no verdict
recorded**. This directory is empty: the scenario writes artifacts only after the pause is
observed, and both attempts were cut before that point.

## What we know

- The **equivalent path passes inside S1**: the support-agent corpus task `pos-004`
  (escalation) drives the identical chain — destructive call → escalation → pause →
  approval → re-dispatch → completion — via benchmark auto-approval, on every
  certification run. So the escalation/approval/re-dispatch machinery works against this
  exact agent and tool.
- What S6 adds over S1's version: the approval is granted through the **operator surface**
  (`GET /approvals` → approve → resume) rather than the benchmark's internal decider, and in
  **production** context rather than sandbox.

## What remains

One uninterrupted run. Expected duration: seconds (the escalation pauses near-instantly;
approve+resume is two API calls; the re-drive completes in <100 ms based on S1 latencies).
If it hangs, the interesting suspects are:
- approval-list visibility across the escalation boundary (does `/approvals` show the
  pending record for the run?),
- resume ordering (transition-then-resume, #129) for a production-context escalation.

## Evidence

None yet — this is the only scenario directory with no artifacts.
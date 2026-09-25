# S6 — destructive-tool (INCOMPLETE — no verdict)

**Scenario:** submit a `support-agent` production run with an unknown-topic task; the agent
escalates via the destructive `pagerduty.acknowledge` call → run must pause on escalation →
operator approves via `/approvals` → run resumes and completes.

**Status:** ⚠️ incomplete — aborted mid-run three times (operator interrupts); **no verdict
recorded**. The scenario directory contains only the harness artifacts written before the
aborts: the pause wait, approval lookup, and resume were never observed to completion.

## What we know

- The **equivalent path passes inside S1**: the support-agent corpus task pos-004
  (escalation) drives the identical chain — destructive call → escalation → pause →
  approval → re-dispatch → completion — via benchmark auto-approval, on every
  certification run, against this exact agent and tool.
- What S6 adds over S1's version: the approval is granted through the **operator surface**
  (`GET /approvals` → approve → resume) rather than the benchmark's internal decider, and
  in **production** context rather than sandbox.

## What remains

One uninterrupted run. Expected duration: seconds (the escalation pauses near-instantly;
approve+resume is two API calls; the re-drive completes in <100 ms based on S1 latencies).

## Evidence

None yet — this is the only scenario directory with no recorded verdict.
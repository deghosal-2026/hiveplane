# PRD 08: Risks

## TLDR

The hardest parts are abstraction and timing: abstracting runtimes without becoming vague, and building enough control loop before building UI.

## Risks

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | Abstracting runtimes without becoming vague | High | Start with two concrete adapters and force the common contract from real workloads |
| R2 | Over-designing for scale too early | High | Local-first Docker Compose; defer multi-tenancy to v0.4.0 |
| R3 | Policy becomes annoying rather than usable | Medium | Policy is visible config; ship sensible defaults and inspect why a decision was made |
| R4 | Proving value beyond a dashboard | High | The control loop (enforce, pause, resume, stop) ships before rich UI |
| R5 | Building too much UI before the control loop is solid | High | MVP UI is a fleet list and a run detail page only |
| R6 | Adapter fragmentation slows onboarding | Medium | Conformance suite + a reference raw-worker adapter |
| R7 | Long-running runs outlive the control plane | Medium | Durable run state in PostgreSQL; resume after restart |

## Hard Parts

- defining the right common workload contract — too much and it is a framework wrapper, too little and it is a dashboard
- real-time budget enforcement without penalizing legitimate long runs
- keeping intervention semantics consistent across heterogeneous runtimes

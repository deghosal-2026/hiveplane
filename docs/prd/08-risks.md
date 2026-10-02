# PRD 08: Risks

## TLDR

The hardest parts are certification design, abstraction, and timing: designing a benchmark that is meaningful without being gameable, abstracting runtimes without becoming vague, and building the control loop + certification before building breadth.

## Risks

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | **Benchmark is gameable** — a crafted corpus passes a bad agent | High | Corpus is reviewed and versioned; production threshold is separate from staging; corpus changes require approval; include negative/counterexample tasks |
| R2 | **Certification gives false confidence** — passing the benchmark doesn't mean the agent is safe in production | High | Certification is necessary, not sufficient; budget, policy, sandbox, and approvals remain enforced at runtime; certification is one layer, not the only one |
| R3 | **Benchmark corpus is too thin** — not enough tasks to be meaningful | High | Start with a focused corpus per workload type; grow via community contribution; ship a seeded corpus with v0.1.0 |
| R4 | **Drift detector over-triggers** — false quarantines erode trust | Medium | Drift is measured against the agent's own baseline, not a global standard; include a grace margin; false-quarantine rate is a tracked metric |
| R5 | **Drift detector under-triggers** — slow decay evades detection | Medium | Combine scheduled re-cert with trigger-based re-cert (failure spike → immediate re-cert) |
| R6 | Abstracting runtimes without becoming vague | High | Start with two concrete adapters and force the common contract from real workloads |
| R7 | Over-designing for scale too early | High | Local-first Docker Compose; multi-tenancy and Helm ship only in the final v0.2.0 Complete Fleet OS release |
| R8 | Policy becomes annoying rather than usable | Medium | Policy is visible config; ship sensible defaults and inspect why a decision was made |
| R9 | Proving value beyond a dashboard | High | The control loop + certification gate ships before rich UI |
| R10 | Building too much UI before the control loop is solid | High | MVP UI is a fleet list, run detail, and certification dashboard only |
| R11 | Adapter fragmentation slows onboarding | Medium | Conformance suite + a reference raw-worker adapter |
| R12 | Long-running runs outlive the control plane | Medium | Durable run state in PostgreSQL; resume after restart |
| R13 | **Model swap attack** — certified on A, runs on B | High | Certification binds to model identity; runtime checks attestation; mismatch blocks |
| R14 | **Benchmark corpus maintenance burden** — corpora rot as systems change | Medium | Version corpora; flag stale corpora; periodic review |
| R15 | **Sandbox escape** — destructive run reaches control-plane resources | High | Separate execution context; restricted network egress; no shared filesystem; resource caps |

## Hard Parts

- designing a benchmark that is meaningful, reproducible, and not gameable — this is the core research problem
- defining the right common workload contract — too much and it is a framework wrapper, too little and it is a dashboard
- real-time budget enforcement without penalizing legitimate long runs
- keeping intervention semantics consistent across heterogeneous runtimes
- balancing certification rigor with onboarding friction — if certification takes a day, nobody uses it
- drift detection that catches real decay without false-quarantining healthy agents

## What Could Kill the Project

1. **Certification is perceived as theater.** If the benchmark is trivial or gameable, certification becomes a checkbox, not a gate. The benchmark must be real.
2. **Certification is too slow.** If it takes hours, teams will bypass it. It must run in minutes.
3. **The adapter contract is wrong.** Too tight = framework wrapper. Too loose = dashboard. Getting this wrong undermines the entire pluggability thesis.

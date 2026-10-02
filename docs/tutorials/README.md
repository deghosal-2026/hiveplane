# HivePlane Tutorials

Hands-on, end-to-end walkthroughs. Each tutorial assumes the local stack is running and
builds on the previous one.

| # | Tutorial | You will learn |
|---|----------|----------------|
| 1 | [Getting Started](01-getting-started.md) | Start the stack, register, certify, run, and intervene |
| 2 | [Certify, Promote & Survive Drift](02-certify-promote-drift.md) | The artifact hash, the promotion gate, drift, quarantine, reinstatement |
| 3 | [Autonomy: Triggers & Pipelines](03-autonomy-triggers-pipelines.md) | Trigger a run from an event and chain agents into a pipeline |

**Before you begin:** Docker, plus `pip install hiveplane==0.2.0`. Bring the stack up with
`scripts/dev-up.sh` (see tutorial 1). New to the concepts? Read the
[Architecture Tour](../architecture-tour.md) first. Operating a live plane? Keep the
[Operator Runbook](../runbooks/operator-runbook.md) open.

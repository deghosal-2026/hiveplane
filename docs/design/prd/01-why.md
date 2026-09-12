# PRD 01: Why — Problem Statement and Motivation

## TLDR

Most teams can build one impressive agent. Very few can operate ten agents with consistent policy, observability, and spend discipline. The bottleneck is not model quality — it is platform operations. HivePlane is the control plane that treats agents as workloads: registered, budgeted, governed, observable, and interruptible.

## The Gap

Agent frameworks solve orchestration inside one workflow. They do not solve the fleet-level operating model. Once a team runs multiple agents across CI, incident response, repo analysis, delivery workflows, docs, and governance, the same operational pain appears everywhere:

1. **No shared runtime model.** Each agent is operated differently, creating local optima and global confusion.
2. **Weak governance.** Tool permissions, approval boundaries, cost budgets, and escalation policies are implemented differently or not at all.
3. **Poor debuggability.** When an agent misbehaves, operators jump across separate dashboards, log formats, and ad hoc scripts.
4. **No fleet-level visibility.** Teams cannot answer simple questions: which agents are expensive but low-value, which teams use which agents, which agents fail verification most, which policies generate the most escalations.

## The Solution

HivePlane defines desired state for agent workloads and observes actual runtime state:

- **Register** — every agent is a first-class workload with owner, runtime, allowed tools, model strategy, budgets, escalation contacts, required approvals, and an observability contract.
- **Operate** — submit tasks, track run state (queued → running → paused/completed/failed), and enforce budgets during execution.
- **Intervene** — pause, inspect, resume with modified context, or terminate a run when it becomes unsafe or uneconomical.
- **Review** — a fleet view of health, spend, failures, approvals, and policy violations by team and agent.

The strategic reason this matters: the AI ecosystem has many builders and not enough operators. The question advanced teams now care about is not "can the agent do something useful?" but "can I run a fleet of them safely, predictably, and transparently?"

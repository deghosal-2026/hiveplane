# PRD 04: Users and Critical User Journeys

## TLDR

Primary user: platform engineering teams running multiple internal AI agents. The critical journeys are registering a workload, submitting and tracking a run, intervening on policy or budget, and the weekly fleet review.

## Users

### Primary

Platform engineering teams running or planning to run multiple internal AI agents.

### Secondary

- SRE or AI platform teams responsible for runtime reliability and governance
- engineering enablement teams building reusable AI workflows
- OSS maintainers building multi-agent platforms and wanting a better operating model

### Not For

- hobbyists who only run one simple chat-style agent
- teams looking for a no-code business assistant tool
- users who want a thin wrapper over one framework without platform concerns

## Critical User Journeys

### CUJ-1: Register a New Agent

Platform engineer defines the workload manifest (owner, runtime, tool permissions, budget rules, approval requirements, observability metadata); HivePlane validates it; the agent appears in the fleet catalog.

### CUJ-2: Submit and Track a Run

Client submits a task; the control plane assigns a run ID and adapter, persists state transitions, tracks budget burn and tool activity, and lets the caller inspect the run live.

### CUJ-3: Policy or Budget Intervention

A run exceeds budget or hits a guarded tool call; the policy engine marks it for escalation; an operator sees the evidence and approves continuation, edits state, or stops the run.

### CUJ-4: Fleet Review

A team lead opens the dashboard weekly, reviews spend, usage, failures, and approvals by team, identifies low-value or risky agents, and tightens budgets or policies from one place.

## See Also

- [Core workflows](../../../README.md#core-workflows)
- [Success metrics](07-success-metrics.md)

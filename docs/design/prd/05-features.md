# PRD 05: Features

## TLDR

v0.1.0 proves the control loop with three real agents. Later versions add approvals UX, richer policy, multi-runtime support, replay, and multi-tenancy.

## v0.1.0 — Prove the Control Loop

- agent registry with owner, runtime type, and policy metadata
- task submission API with persistent run state
- budget enforcement per run
- pause, resume, and cancel controls
- basic audit log
- trace and metric export
- minimal operator UI with fleet list and run detail
- raw Python worker adapter + one LangGraph example adapter

## v0.2.0 — Governance Surface

- approval queue UI
- richer policy conditions
- budget analytics by agent/team
- stronger adapter contract

## v0.3.0 — Multi-Runtime & Reliability

- multi-runtime support
- state diff and replay helpers
- reliability metrics and SLO hooks

## v0.4.0 — Scale & Tenancy

- multi-tenant support
- ROI dashboards
- Helm chart and reference cluster deployment

## In Scope (Overall)

- fleet registry and workload model
- run lifecycle management
- budget and quota enforcement
- approval and intervention hooks
- trace, metric, and audit integration
- simple but real operator UI

## Out of Scope (Initial Versions)

- building a new agent framework
- replacing model providers
- generalized workflow authoring UI
- full enterprise IAM complexity
- autonomous self-healing logic for every failure mode

# PRD 03: Landscape

## TLDR

Orchestration frameworks (LangGraph, AutoGen, CrewAI) build workflows. Observability tools trace them. Cost tools report after the fact. Governance tools enforce one control. None of them operate a heterogeneous fleet of agents as governed workloads from one place.

## Adjacent Categories

| Category | Examples | What it solves | What it leaves open |
|----------|----------|----------------|---------------------|
| Agent frameworks | LangGraph, AutoGen, CrewAI | Building one workflow | Fleet-level operating model |
| Agent observability | execution tracers, trace viewers | Seeing what one run did | Cross-fleet governance and intervention |
| Cost trackers | token/cost dashboards | After-the-fact spend | Real-time budget enforcement |
| Guardrail libraries | input/output filters | One safety boundary | Tool permissions, approvals, escalation |
| MCP tooling | MCP servers and gateways | Standard tool interface | Which agent may call which tool, under what policy |
| Kubernetes control planes | k8s | Workloads in general | Agent-specific budgets, approvals, run semantics |

## The Wedge

HivePlane does not compete with frameworks or observability vendors. It is the layer above them: the control plane that answers "what agents exist, what can they access, how much are they spending, what state are they in, and who gets paged when they fail."

## Notes

Landscape research to be expanded with concrete repositories, star counts, and positioning during v0.1.0.

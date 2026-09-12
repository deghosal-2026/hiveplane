# PRD 03: Landscape

## TLDR

The agent ecosystem has frameworks for building, tools for observing, and platforms for running — but nobody certifies. HivePlane's wedge is the certification pipeline: agents must prove themselves against a reproducible benchmark before touching production. Every competitor lets agents run the moment they're connected.

## Concrete Competitor Map

| Repo | ★ | Category | What it does | What it lacks |
|------|---|----------|--------------|---------------|
| keephq/**keep** | 12.3k | AIOps / alert mgmt | Alert dedup, correlation, enrichment, AI summarization, workflows, dashboards | No agent workload model, no certification, no budget enforcement, no safe execution |
| Tracer-Cloud/**opensre** | 11.0k | AI SRE framework | Build AI SRE agents, 60+ tools, RL eval environment, e2e tests | Builds agents, doesn't *operate* a fleet; eval env exists but doesn't gate production; no control plane, no budget/approval/intervention |
| kubeshark/**kubeshark** | 12.1k | Network observability | eBPF L4/L7 traffic, K8s context, AI-agent-queryable | Observability only, no agent governance |
| k8sgpt-ai/**k8sgpt** | 8.2k | K8s diagnostics | LLM-analyzes K8s resources, finds issues | Single-agent, no fleet, no certification, no budget/policy |
| coroot/**coroot** | 7.9k | Observability + RCA | eBPF metrics/logs/traces/profiles, AI-powered RCA, SLOs, cost monitoring | Observability platform, not an agent control plane; no agent workload model |
| GoogleCloudPlatform/**kubectl-ai** | 7.6k | K8s assistant | LLM-powered kubectl | CLI tool, not a platform |
| OneUptime/**oneuptime** | 7.6k | Incident mgmt | Monitoring, alerting, incident tracking, status pages | Traditional incident tool, no agent governance |
| HolmesGPT/**holmesgpt** | 3.3k | CNCF SRE agent | Agentic loop, 40+ toolsets, Operator mode (24/7), bidirectional alert integ, PR fixes | Single agent, no fleet model, no certification, no budget/approval/sandbox; strong on tools, weak on governance |
| robusta-dev/**robusta** | 3.1k | K8s alert automation | Prometheus alerts, AI enrichment, auto-remediation | Alert-centric, no agent workload model |
| openocta/**openocta** | 3.1k | AIOps agent | OS-level AIOps agent for Windows/macOS | Single-agent, desktop-focused |
| alibaba/**SREWorks** | 2.0k | AIOps platform | Cloud-native DataOps & AIOps platform | Heavy platform, not agent-certification-focused |
| Tencent/**Metis** | 1.8k | AIOps learnware | AIOps ML toolkit | ML library, not an operating platform |
| logpai/**loglizer** | 1.4k | Log anomaly detection | ML toolkit for log-based anomaly detection | ML library, no agent governance |
| codefuse-ai/**codefuse-chatbot** | 1.3k | DevOps assistant | Multi-agent DevOps assistant with RAG | Chatbot, not a control plane |
| ongridio/**ongrid** | 1.0k | Ops agent | Finds + fixes root cause from Slack | Single-agent, action-oriented, no fleet/certification |
| salesforce/**PyRCA** | 569 | Causal RCA library | Python ML library for root cause analysis | Library, not a platform |
| microsoft/**OpenRCA** | 418 | RCA benchmark | ICLR'25: can LLMs locate root cause? | Benchmark paper, not a platform — but a key eval target |
| Arvo-AI/**aurora** | 411 | Agentic incident mgmt | LangGraph agents investigate across AWS/Azure/GCP/K8s, auto-RCA, postmortems, PR fixes, artifacts, actions | Closest agentic RCA; still retrieval/triage, no certification, no fleet model, no budget/sandbox; strong on integrations |
| schickling/**dilagent** | 106 | Hypothesis-driven debugging | "Deep research for bugs" via hypothesis-driven agent | Closest conceptual analog for hypothesis generation; code bugs, not fleet operations |

## Adjacent Categories — What Each Leaves Open

| Category | Examples | What it solves | What it leaves open |
|----------|----------|----------------|---------------------|
| Agent frameworks | LangGraph, AutoGen, CrewAI | Building one workflow | Fleet operating model, certification, budget, safe execution |
| Agent observability | exec-tracers, trace viewers | Seeing what one run did | Cross-fleet governance, intervention, certification |
| Cost trackers | token/cost dashboards | After-the-fact spend | Real-time enforcement, ROI, showback |
| Guardrail libraries | input/output filters | One safety boundary | Tool permissions, approvals, escalation, sandbox |
| MCP tooling | MCP servers, mcp-fabric | Standard tool interface | Which agent may call which tool, under what policy, certified to do so |
| Kubernetes control planes | k8s | Workloads in general | Agent-specific budgets, approvals, run semantics, certification |
| Eval frameworks | agent-eval-forge, SWE-bench | Evaluating agent quality | **Nobody connects eval to production gating** |

## The Wedge — Certification-Gated Production

Every competitor connects an agent and lets it run. Opensre builds an eval environment but doesn't gate production on it. Agent-eval-forge provides the harness but doesn't connect it to an operating platform. SWE-bench proved the paradigm for coding agents but nobody brought it to production agent fleets.

**HivePlane is the connection:** the certification pipeline that says "this agent is not allowed in production until it proves itself against a benchmark, and it will be quarantined if it drifts."

This is not a feature. It is the thesis. It is the thing that makes HivePlane a platform operators can trust, not just a dashboard they can watch.

## Positioning Statement

> Frameworks build agents. Observability tools watch them. HivePlane operates them — and certifies them before they touch production. It is the control plane for agent fleets that need to earn trust, not just run.

## See Also

- [Why](01-why.md)
- [Features](05-features.md)

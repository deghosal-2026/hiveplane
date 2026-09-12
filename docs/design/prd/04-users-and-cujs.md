# PRD 04: Users and Critical User Journeys

## TLDR

Primary user: platform engineering teams running multiple internal AI agents. The critical journeys span the full operating loop — register, **certify**, trigger, track, intervene, promote through eval, onboard tools, review the fleet, and deliver results. Certification is not a side feature; it is a journey every agent must complete before touching production.

## Users

### Primary

Platform engineering teams running or planning to run multiple internal AI agents.

### Secondary

- SRE or AI platform teams responsible for runtime reliability and governance
- engineering enablement teams building reusable AI workflows
- agent authors across product teams who want their agent to run on the platform
- OSS maintainers building multi-agent platforms and wanting a better operating model

### Not For

- hobbyists who only run one simple chat-style agent
- teams looking for a no-code business assistant tool
- users who want a thin wrapper over one framework without platform concerns
- teams that want incident RCA, postmortems, or service maps (those are adjacent tools, not HivePlane's job)

## Critical User Journeys

### CUJ-1: Register and Certify a New Agent

Platform engineer defines the workload manifest (owner, runtime, tool permissions, budget rules, approval requirements, model strategy, observability contract, trigger rules, **benchmark corpus reference**). HivePlane validates it and runs the agent against its benchmark in a controlled environment. If it passes at staging threshold, the agent becomes `provisional` and can run in staging. If it passes at production threshold, it becomes `certified` and can run in production. The certification is a signed attestation: benchmark version, model, eval results, timestamp, environment.

**This is the SWE-bench journey.** An agent is not production-ready because someone watched a demo. It is production-ready because it passed a reproducible benchmark, and HivePlane can prove it.

### CUJ-2: Submit and Track a Run

Client submits a task; the control plane checks the agent's certification status, assigns a run ID and adapter, persists state transitions, tracks budget burn and tool activity, and lets the caller inspect the run live. Long tool outputs are shaped at the boundary (filter, truncate, budget) so context windows don't silently blow. If the agent is not certified for the target environment, submission is refused.

### CUJ-3: Trigger a Run from an Event

An alert, a GitHub PR event, a cron tick, or a webhook fires; HivePlane matches it to a workload's trigger rule, checks certification, and starts a run automatically — no human submits. Scheduled and watch modes keep agents checking things 24/7. This is the difference between "we can run an agent" and "the platform operates the agent for us."

### CUJ-4: Policy or Budget Intervention

A run exceeds budget or hits a guarded tool call; the policy engine marks it for escalation; an operator sees the evidence and approves continuation, edits state, or stops the run. Policy is context-aware: the same tool is allowed in staging and gated in production, over public docs vs. PII, under different blast-radius scores.

### CUJ-5: Run a Destructive Action Safely

An agent wants to perform a destructive or production-affecting action. HivePlane runs it in an isolated execution context (sandbox, resource caps, output budget), requires approval, executes only after consent, records the full action and result, and writes the outcome back to where the team works (Slack/Teams/Jira/PR/webhook).

### CUJ-6: Promote a Workload Change Through Re-Certification

An author changes a prompt, model, tool, or orchestration on a live workload. HivePlane refuses to promote the change until **re-certification** passes — the workload is re-run against its benchmark and the new certification is compared against the previous one. If the pass rate dropped or a critical task regressed, promotion is blocked. The author gets a replayable trace showing what broke. No silent regressions reach the fleet.

### CUJ-7: Detect Drift and Quarantine

A `certified` agent has been running in production for two weeks. Its performance is decaying — maybe the model provider changed something, maybe the tool API shifted, maybe the prompt is stale. HivePlane's drift detector runs a periodic re-certification. If the agent drops below threshold, it is auto-quarantined: production runs are blocked, the owning team is notified, and the agent's fleet status changes to `quarantined` with the evidence. The team fixes it, re-certifies, and the agent is promoted back.

**This is the journey no competitor has.** Drift detection with auto-quarantine is the difference between "we hope the agent is still good" and "we know it's not, and we stopped it."

### CUJ-8: Onboard a Tool via MCP

A platform engineer connects a tool (database, observability backend, cloud API, internal service) through the MCP tool layer (built on mcp-fabric). The tool gets a trust level and a tool ID; workloads reference it by ID; policy decides which agents may call it and under what conditions. Tool onboarding is separate from agent onboarding.

### CUJ-9: Fleet Review

A team lead opens the dashboard weekly. They review:

- **Certification status** — which agents are certified, provisional, or quarantined; when each was last certified; pass rates and trends
- **Spend** — cost showback by team and agent, including waste detection and ROI flags (expensive-but-low-value agents)
- **Health** — readiness, recent failure rate, SLO status, drift indicators
- **Approvals** — pending and resolved escalations
- **Policy** — which policies generate the most escalations

They tighten budgets, retire or quarantine unhealthy agents, re-certify drifted ones, and promote or roll back changes — all from one place.

### CUJ-10: Receive a Result Where You Already Work

When a run completes, fails, or escalates, HivePlane fans the result out to the channels the owning team configured — Slack/Teams message, Jira update, GitHub PR comment, or webhook — with a trace link and, for certifications, a link to the attestation. Operators do not have to come to HivePlane to know something happened.

## Journey → Capability Map

| CUJ | Capabilities required |
|-----|----------------------|
| CUJ-1 | registry, manifest validation, **benchmark runner, certification engine, attestation** |
| CUJ-2 | run lifecycle, execution API, **certification check at admission**, tool-output shaping |
| CUJ-3 | trigger rules, webhook/cron ingest, scheduled/watch modes, **certification check** |
| CUJ-4 | policy engine, approvals, context-aware policy, blast-radius scoring |
| CUJ-5 | execution isolation, resource/output caps, write-back |
| CUJ-6 | **re-certification, promotion gate, regression diff, replay trace** |
| CUJ-7 | **drift detector, auto-quarantine, periodic re-certification** |
| CUJ-8 | MCP tool registry, trust levels |
| CUJ-9 | fleet view, cost showback/ROI, agent health/SLO/drift, **certification dashboard** |
| CUJ-10 | result fan-out (Slack/Teams/Jira/PR/webhook) |

## See Also

- [Features](05-features.md)
- [Success metrics](07-success-metrics.md)
- [Security baseline](06-security-baseline.md)

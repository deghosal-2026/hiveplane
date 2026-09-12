# PRD 01: Why — Problem Statement and Motivation

## TLDR

SWE-bench gave coding agents scalable training data and clear pass/fail feedback. Production agent fleets have no equivalent. Teams register agents, change prompts, swap models, and ship to production with zero benchmark evidence — then act surprised when a regression reaches a customer. HivePlane is the control plane that closes this gap: agents must be **certified** against a reproducible benchmark before they operate in production, and re-certified when they drift. No other agent platform gates production on evidence.

## The Gap — Two Missing Layers

### Layer 1: No Operating Model (the fleet problem)

Agent frameworks solve orchestration inside one workflow. They do not solve the fleet-level operating model. Once a team runs multiple agents across CI, incident response, repo analysis, delivery workflows, docs, and governance, the same pain appears everywhere:

1. **No shared runtime model.** Each agent is operated differently — different budget logic, different logs, different approvals, different ownership.
2. **No trigger model.** Agents only run when a human remembers to invoke them. Alerts fire, PRs merge, cron ticks — nothing happens. The platform cannot operate the agent on the team's behalf.
3. **No safe execution.** Destructive and production-affecting actions run in the same context as read-only work. No sandbox, no resource caps, no output budgeting.
4. **No change governance.** Prompt, model, tool, and orchestration changes ship with no eval gate. A regression reaches the fleet silently.
5. **No fleet-level visibility.** Teams cannot answer: which agents are expensive but low-value, which are drifting, which fail verification most, which policies generate the most escalations.
6. **No delivery.** Results stay inside the platform. Teams have to come looking — they don't learn that a run completed, failed, or escalated where they already work.

### Layer 2: No Certification (the SWE-bench problem)

SWE-bench proved that agents improve when they have a standardized, reproducible benchmark with clear pass/fail. Coding agents now have scalable training data and feedback loops. **Production agent fleets have no equivalent.**

Today, an agent is "production-ready" because:

- someone watched it succeed on a demo
- someone ran it on 3 examples that happened to work
- someone changed a prompt and eyeballed the output

There is no benchmark. No held-out eval set. No reproducible certification. No drift detection. No gate that says "this agent is not allowed in production until it proves itself."

Every competitor — opensre, keep, aurora, holmesgpt — lets agents run the moment they're connected. Opensre is building an eval *environment* but does not gate production on it. Nobody certifies agents before letting them operate on real systems.

**HivePlane does.** Every workload carries a certification status:

| Status | Meaning | Where it can run |
|--------|---------|------------------|
| `uncertified` | Registered, never benchmarked | Sandbox only |
| `provisional` | Passed benchmark at staging threshold | Staging |
| `certified` | Passed benchmark at production threshold + survived N runs without regression | Production |
| `quarantined` | Failed re-certification or drifted below threshold | Runs blocked |

A certification is an attestation: benchmark version, model, eval results, timestamp, environment. It is reproducible, versioned, and auditable. When a workload changes (prompt, model, tool, orchestration), re-certification is required before promotion. When an agent drifts in production, periodic re-certification catches it and quarantines the agent before a customer does.

This is the difference between "we run agents" and "we run agents we can trust."

## The Solution

HivePlane is two layers in one platform:

### Layer 1: The Control Plane (operate the fleet)

- **Register** — every agent is a first-class workload with owner, runtime, allowed tools, model strategy, budgets, escalation contacts, required approvals, trigger rules, and an observability contract.
- **Trigger** — webhooks, alerts, GitHub PR events, and cron rules auto-start runs. Scheduled and watch modes keep agents checking things 24/7.
- **Operate** — submit or trigger tasks, track run state, enforce budgets, and shape tool outputs at the boundary.
- **Govern** — context-aware policy (staging vs. production, public vs. PII, blast-radius scoring), team policy packs.
- **Execute safely** — destructive actions run in isolated execution contexts with resource caps and require approval.
- **Intervene** — pause, inspect, resume with modified context, or terminate a run.
- **Deliver** — fan results out to Slack, Teams, Jira, GitHub PR comments, or webhooks with trace links.
- **Review** — fleet view of health, spend, cost showback, ROI flags, and policy violations by team and agent.

### Layer 2: The Certification Pipeline (earn the right to operate)

- **Benchmark** — each workload type has a benchmark corpus of tasks with known expected outcomes (the SWE-bench equivalent for production agents).
- **Certify** — on registration or change, run the workload against the benchmark in a controlled environment. Pass/fail is deterministic and reproducible.
- **Attest** — each certification is a signed attestation: benchmark version, model, eval results, timestamp, environment, signer.
- **Gate** — only `certified` agents run in production contexts. Manifest changes require re-certification. No silent regressions reach the fleet.
- **Drift** — periodic re-certification detects performance decay. Drifting agents are auto-quarantined before customers notice.
- **Replay** — failed certifications produce a replayable trace so authors can debug what broke.

## Why This Wins

| Property | Competitors | HivePlane |
|----------|-------------|-----------|
| Agent certification before production | Nobody | SWE-bench-style benchmark gate |
| Fleet operating model | Fragments per framework | One control plane |
| Trigger model | Manual or single-source | Webhook + alert + PR + cron + scheduled |
| Safe execution | None or per-framework | Sandbox + resource caps + output shaping |
| Change governance | None | Eval-gated promotion + re-certification |
| Drift detection | None | Periodic re-certification → auto-quarantine |
| Cost showback | After-the-fact dashboards | Real-time enforcement + attribution + ROI |
| Result delivery | Come to the platform | Fan-out to where teams work |

The strategic reason this matters: the AI ecosystem has many builders and not enough operators. The question advanced teams now care about is not "can the agent do something useful?" but **"can I trust this agent in production, and can I prove it?"** HivePlane is the only platform that answers yes with evidence.

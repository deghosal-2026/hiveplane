# HivePlane User Guide

> Status: draft — to be completed with v0.1.0.

## What HivePlane Is

HivePlane is a control plane for operating a fleet of AI agents as first-class workloads. You register each agent, declare who owns it, what tools it may call, what it may spend, and what approvals it requires — then observe and intervene from one operator surface.

## Prerequisites

- Docker (for the reference local stack)
- Python 3.11+ (for the API and worker examples)

## Quick Start

```bash
# 1. Start the local control plane
docker compose up -d

# 2. Register an agent workload
hiveplane register examples/workloads/example-agent.yaml

# 3. Submit a task
hiveplane submit --agent example-agent --task "summarize open PRs"

# 4. Inspect the run
hiveplane runs list
hiveplane runs show <run-id>

# 5. Intervene
hiveplane runs pause <run-id>
hiveplane runs resume <run-id>
hiveplane runs stop <run-id>
```

## Core Concepts

| Concept | Meaning |
|---------|---------|
| **Agent workload** | A registered agent with an owner, runtime, tools, budget, and approval policy |
| **Manifest** | The declarative YAML that defines a workload |
| **Run** | One execution of a workload, with persistent state |
| **Policy** | Rules governing tool permissions, budgets, and approvals |
| **Adapter** | The bridge between HivePlane and a runtime (LangGraph, raw worker) |

See [design/prd/04-users-and-cujs.md](design/prd/04-users-and-cujs.md) for critical user journeys.

## Configuration

Configuration options and environment variables are documented as they land in v0.1.0.

## Troubleshooting

To be completed alongside v0.1.0.

## See Also

- [Docs index](README.md)
- [Workload manifest format](design/workload-manifest-design.md)
- [Adapters](ADAPTERS.md)

# HivePlane Adapters

Runtime adapters are how HivePlane stays framework-agnostic. The control plane owns the workload contract; adapters translate that contract to a concrete runtime.

## Why Adapters

The failure mode to avoid is becoming a wrapper around one framework. If HivePlane knows too much about LangGraph, it stops being a control plane. If it knows too little, it becomes a dashboard with no control. The adapter contract is the boundary that keeps both failure modes away.

## Adapter Contract

An adapter must translate:

| Control-plane concept | Adapter responsibility |
|-----------------------|------------------------|
| Workload manifest | Configure the runtime from declarative state |
| Task submission | Start a run and return a run handle |
| State transition | Report queued → running → paused/completed/failed |
| Tool call metadata | Emit tool calls for policy and audit |
| Spend / token usage | Report usage for budget accounting |
| Intervention | Pause, resume, cancel, and (where supported) edit state |

## Planned Adapters

### v0.1.0

- **Raw Python worker** — reference adapter, no framework dependency
- **LangGraph** — example adapter for a compiled graph

### Later

- Additional runtimes as the adapter contract proves stable.

## Adapter Conformance

Each adapter must pass a conformance suite that exercises the full lifecycle: register → submit → state transitions → budget report → pause → resume → cancel.

Details to be completed in the v0.1.0 WBS (Part 6).

## See Also

- [Runtime adapter design](design/runtime-adapter-design.md)
- [Run lifecycle design](design/run-lifecycle-design.md)

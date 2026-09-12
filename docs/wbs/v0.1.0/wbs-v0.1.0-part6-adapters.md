# WBS v0.1.0 — Part 6: Runtime Adapters

**Milestones:** M11-M12

## Goal

Prove the adapter contract with two concrete runtimes.

## M11 — Adapter Interface & Raw Worker

- [ ] Define the adapter interface (see [D6](../../design/runtime-adapter-design.md))
- [ ] Implement the raw Python worker reference adapter
- [ ] Route tool calls through the policy boundary
- [ ] Report usage and state transitions

## M12 — LangGraph Adapter & Conformance

- [ ] Implement a LangGraph example adapter
- [ ] Adapter conformance suite (register → submit → transition → usage → pause → resume → cancel)
- [ ] Both adapters pass conformance

## Exit Criteria

- [ ] Two adapters run through the same lifecycle
- [ ] Conformance suite is green for both
- [ ] Exit gate checklist passed

## See Also

- [Runtime adapter design](../../design/runtime-adapter-design.md)
- [Adapters](../../ADAPTERS.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

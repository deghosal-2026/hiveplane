# WBS v0.1.0 — Part 9: Operator UI & CLI

**Milestones:** M16-M17

## Goal

Give operators one surface to see the fleet, inspect a run, act, and review spend.

## M16 — CLI

- [ ] `hiveplane register <manifest>`
- [ ] `hiveplane submit --agent <name> --task <task>`
- [ ] `hiveplane runs list|show <id>`
- [ ] `hiveplane runs pause|resume|stop <id>`
- [ ] `hiveplane approvals list|approve|deny`

## M17 — Minimal Operator UI

- [ ] Fleet list (owner, state counts, recent failures, budget burn)
- [ ] Run detail (state timeline, tool calls, cost, trace link)
- [ ] Approval queue with approve/deny
- [ ] Spend view by workload and team

## Exit Criteria

- [ ] An operator can inspect and stop a bad run from the UI
- [ ] Approvals can be resolved from the UI
- [ ] Exit gate checklist passed

## See Also

- [Operator UI design](../../design/operator-ui-design.md)
- [User guide](../../USER_GUIDE.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

# WBS v0.1.0 — Part 4: Policy Engine & Approvals

**Milestones:** M8-M9

## Goal

Enforce tool permissions and approval requirements at the control-plane boundary, with explainable decisions.

## M8 — Policy Evaluation

- [ ] Evaluate allow/deny/escalate from manifest + run context
- [ ] Evaluation order: deny → allow → approval class → default deny
- [ ] Decision objects carry `reason` and originating `rule`

## M9 — Approvals

- [ ] Escalation creates a pending approval with evidence
- [ ] Approve/deny endpoints
- [ ] Resume or fail the run based on the decision
- [ ] Slack/webhook notification hook (nice-to-have)

## Exit Criteria

- [ ] A guarded tool call escalates and blocks until approved
- [ ] Every decision is explainable and audited
- [ ] Exit gate checklist passed

## See Also

- [Policy engine design](../../design/policy-engine-design.md)
- [Security baseline](../../design/prd/06-security-baseline.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

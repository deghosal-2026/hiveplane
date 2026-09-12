# WBS v0.1.0 — Part 10: Field Test

**Milestone:** M18

## Goal

Run three real agents through the same lifecycle and prove the control loop end to end.

## M18 — Field Test

- [ ] Define three real workloads (e.g. a repo agent, a docs agent, an incident agent)
- [ ] Register all three; run each through queued → running → completed
- [ ] Seed an over-budget run and a guarded tool call; verify escalation and intervention
- [ ] Restart the control plane mid-run; verify resume
- [ ] Collect metrics and publish the field test report

## Exit Criteria

- [ ] All three agents operate through the same contract
- [ ] Field test report published (`docs/field-test/v0.1.0/FIELD_TEST_REPORT.md`)
- [ ] Exit gate checklist passed

## See Also

- [Field test plan](../../field-test/v0.1.0/field-test-plan.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

# WBS v0.1.0 — Part 12: Field Test

**Milestone:** M23 · **Issues:** #56-#59

## Goal

Run three real agents through the certified lifecycle and produce evidence for every v0.1.0 release gate.

## M23 — Field Test

**Issues:** [#56](https://github.com/deghosal-2026/hiveplane/issues/56) · [#57](https://github.com/deghosal-2026/hiveplane/issues/57) · [#58](https://github.com/deghosal-2026/hiveplane/issues/58) · [#59](https://github.com/deghosal-2026/hiveplane/issues/59)

- [ ] [#56](https://github.com/deghosal-2026/hiveplane/issues/56) — Define and register three real workloads
- [ ] [#57](https://github.com/deghosal-2026/hiveplane/issues/57) — Certification and governance field scenarios
- [ ] [#58](https://github.com/deghosal-2026/hiveplane/issues/58) — Lifecycle, intervention, fan-out, and report
- [ ] [#59](https://github.com/deghosal-2026/hiveplane/issues/59) — Nightly simulator + CI regression harness

### Scenarios

| # | Scenario | Evidence required |
|---|----------|-------------------|
| S1 | Certify all three agents via benchmark | attestations signed + verified on read |
| S2 | Uncertified agent attempts production | refused with specific error |
| S3 | Model-swap (cert on A, run on B) | blocked |
| S4 | Seeded manifest change regresses | promotion gate blocks, diff produced |
| S5 | Over-budget run | blocked/escalated |
| S6 | Destructive tool call | sandbox caps + approval required |
| S7 | Large tool output | shaped before reaching agent |
| S8 | Pause → restart control plane → resume | context intact |
| S9 | Result fan-out | delivered to Slack + webhook |

**Done when:** every scenario reproduces deterministically, `FIELD_TEST_REPORT.md` is published, and the nightly simulator + CI harness keep the evidence fresh.

## Dependencies

- All prior parts.

## Exit Gate (M23)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Field test plan](../../field-test/v0.1.0/field-test-plan.md)
- [Field test report](../../field-test/v0.1.0/FIELD_TEST_REPORT.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

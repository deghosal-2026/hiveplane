# WBS v0.1.0 — Part 5: Policy Engine & Approvals

**Milestones:** M11-M12 · **Issues:** #32-#35

## Goal

Every tool call is a policy decision with a reason. Context changes the decision. Guarded actions wait for a human.

## M11 — Policy Evaluation Engine

**Issues:** [#32](https://github.com/deghosal-2026/hiveplane/issues/32) · [#33](https://github.com/deghosal-2026/hiveplane/issues/33)

- [ ] [#32](https://github.com/deghosal-2026/hiveplane/issues/32) — Deny-by-default policy evaluation with explainable decisions
- [ ] [#33](https://github.com/deghosal-2026/hiveplane/issues/33) — Context-aware policy (environment, data sensitivity, blast radius)

**Done when:** deny-by-default is verified; every decision carries a reason and rule id; the same tool is allowed in staging and gated in production; team policy packs apply.

## M12 — Approvals & Notifications

**Issues:** [#34](https://github.com/deghosal-2026/hiveplane/issues/34) · [#35](https://github.com/deghosal-2026/hiveplane/issues/35)

- [ ] [#34](https://github.com/deghosal-2026/hiveplane/issues/34) — Escalation to pending approval with evidence
- [ ] [#35](https://github.com/deghosal-2026/hiveplane/issues/35) — Slack notification on approval-needed

**Done when:** a guarded tool call blocks until decided; approve resumes and deny fails with a recorded reason; escalation pings Slack without blocking the run.

## Dependencies

- Part 2 (tools + trust levels)
- Part 4 (run lifecycle hooks)

## Exit Gate (M11, M12)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Policy engine design](../../design/policy-engine-design.md)
- [MCP tool registry design](../../design/mcp-tool-registry-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

# WBS v0.1.0 — Part 7: Execution Sandbox & Tool-Output Shaping

**Milestones:** M14-M15 · **Issues:** #38-#41

## Goal

Isolate destructive runs and keep large tool payloads out of the context window. This is the safety substrate the certification benchmark runs inside.

## M14 — Execution Isolation

**Issues:** [#38](https://github.com/deghosal-2026/hiveplane/issues/38) · [#39](https://github.com/deghosal-2026/hiveplane/issues/39)

- [ ] [#38](https://github.com/deghosal-2026/hiveplane/issues/38) — Sandbox execution context
- [ ] [#39](https://github.com/deghosal-2026/hiveplane/issues/39) — Resource caps and restricted egress

**Done when:** a destructive run executes without reaching control-plane resources; sandboxes are destroyed after the run; a run exceeding a cap is terminated and reported; disallowed egress is blocked.

## M15 — Tool-Output Shaping & Injection Scanning

**Issues:** [#40](https://github.com/deghosal-2026/hiveplane/issues/40) · [#41](https://github.com/deghosal-2026/hiveplane/issues/41)

- [ ] [#40](https://github.com/deghosal-2026/hiveplane/issues/40) — Tool-output shaping pipeline (filter, truncate, budget)
- [ ] [#41](https://github.com/deghosal-2026/hiveplane/issues/41) — Injection scanning of tool outputs

**Done when:** a large payload is bounded before reaching the agent; truncation is visible and recorded; a seeded injection via tool output is blocked or escalated; no false positives on the demo corpus.

## Dependencies

- Part 1 (sandbox + shaping models)
- Part 8 (adapters run inside the sandbox)

## Exit Gate (M14, M15)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Execution sandbox design](../../design/execution-sandbox-design.md)
- [Runtime adapter design](../../design/runtime-adapter-design.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

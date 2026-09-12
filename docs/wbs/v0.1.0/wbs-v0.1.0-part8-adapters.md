# WBS v0.1.0 — Part 8: Runtime Adapters & Conformance

**Milestones:** M16-M17 · **Issues:** #42-#45

## Goal

Prove the pluggability contract with two concrete runtimes and a conformance suite that keeps every adapter honest.

## M16 — Adapter Interface & Raw Worker

**Issues:** [#42](https://github.com/deghosal-2026/hiveplane/issues/42) · [#43](https://github.com/deghosal-2026/hiveplane/issues/43)

- [ ] [#42](https://github.com/deghosal-2026/hiveplane/issues/42) — Adapter interface definition
- [ ] [#43](https://github.com/deghosal-2026/hiveplane/issues/43) — Raw Python worker reference adapter

**Done when:** the interface is documented and typed; the worker completes a run through the full lifecycle; usage is reported and priced; tool calls route through policy and shaping.

## M17 — LangGraph Adapter & Conformance Suite

**Issues:** [#44](https://github.com/deghosal-2026/hiveplane/issues/44) · [#45](https://github.com/deghosal-2026/hiveplane/issues/45)

- [ ] [#44](https://github.com/deghosal-2026/hiveplane/issues/44) — LangGraph example adapter
- [ ] [#45](https://github.com/deghosal-2026/hiveplane/issues/45) — Adapter conformance suite

**Done when:** a LangGraph agent completes a run and maps pause/resume correctly; the conformance suite (register → submit → transition → usage → pause → resume → cancel + sandbox + shaping + model-binding) is green for both adapters and fails the build on a violation.

## Dependencies

- Part 4 (run lifecycle)
- Part 7 (sandbox + shaping)

## Exit Gate (M16, M17)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Runtime adapter design](../../design/runtime-adapter-design.md)
- [Adapters](../../ADAPTERS.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

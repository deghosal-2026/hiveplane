# WBS v0.1.0 — Part 8: Runtime Adapters & Conformance

**Milestones:** M16-M17 · **Issues:** #42-#45

> **Status:** M16-M17 complete — #42-#45 closed (2026-09-16).

## Goal

Prove the pluggability contract with two concrete runtimes and a conformance suite that keeps every adapter honest.

## M16 — Adapter Interface & Raw Worker

**Issues:** [#42](https://github.com/deghosal-2026/hiveplane/issues/42) · [#43](https://github.com/deghosal-2026/hiveplane/issues/43)

- [x] [#42](https://github.com/deghosal-2026/hiveplane/issues/42) — Adapter interface definition
- [x] [#43](https://github.com/deghosal-2026/hiveplane/issues/43) — Raw Python worker reference adapter

**Done when:** the interface is documented and typed; the worker completes a run through the full lifecycle; usage is reported and priced; tool calls route through policy and shaping.

## M17 — LangGraph Adapter & Conformance Suite

**Issues:** [#44](https://github.com/deghosal-2026/hiveplane/issues/44) · [#45](https://github.com/deghosal-2026/hiveplane/issues/45)

- [x] [#44](https://github.com/deghosal-2026/hiveplane/issues/44) — LangGraph example adapter
- [x] [#45](https://github.com/deghosal-2026/hiveplane/issues/45) — Adapter conformance suite

**Done when:** a LangGraph agent completes a run and maps pause/resume correctly; the conformance suite (register → submit → transition → usage → pause → resume → cancel + sandbox + shaping + model-binding) is green for both adapters and fails the build on a violation.

## Dependencies

- Part 4 (run lifecycle)
- Part 7 (sandbox + shaping)

## Exit Gate

### M16 — passed (2026-09-16); push pending

- [x] All tests in the system pass: `pytest` — 519 passed
- [x] Code coverage total > 95% — 97%
- [x] Ruff clean
- [x] Mypy strict clean
- [x] Update all relevant docs affected by this milestone — `docs/ADAPTERS.md`, index, this file
- [x] Verify all issues in this milestone are done
- [x] Close all completed issues — #42, #43
- [ ] Commit and push changes — committed to `main` (`eacbe67`..`03d477c`); push pending

### M17 — passed (2026-09-16); push pending

- [x] All tests in the system pass: `pytest` — 535 passed
- [x] Code coverage total > 95% — see `make cov`
- [x] Ruff clean
- [x] Mypy strict clean
- [x] Update all relevant docs affected by this milestone — `docs/ADAPTERS.md`, index, this file
- [x] Verify all issues in this milestone are done
- [x] Close all completed issues — #44, #45
- [ ] Commit and push changes

## See Also

- [Runtime adapter design](../../design/runtime-adapter-design.md)
- [Adapters](../../ADAPTERS.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

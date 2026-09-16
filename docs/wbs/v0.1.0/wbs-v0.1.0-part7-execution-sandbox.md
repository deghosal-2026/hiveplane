# WBS v0.1.0 — Part 7: Execution Sandbox & Tool-Output Shaping

**Milestones:** M14-M15 · **Issues:** #38-#41

## Goal

Isolate destructive runs and keep large tool payloads out of the context window. This is the safety substrate the certification benchmark runs inside.

## Status

Execution isolation and output shaping have landed. `hiveplane.sandbox` provides
sandbox bookkeeping (`SandboxManager`), a `ProcessSandboxManager` that runs a
command in a subprocess with POSIX resource caps, a wall-clock watchdog, an
ephemeral scratch directory, and guaranteed teardown, and an `EgressGuard` that
enforces the manifest allowlist (cloud metadata always denied). The run
lifecycle provisions a sandbox when a sandboxed run starts and destroys it on any
terminal transition.

`hiveplane.shaping` provides the `ShapingPipeline` (filter, truncate, cumulative
output budget) and the `InjectionScanner` (high-confidence block,
low-confidence escalate). Adapters call the pipeline before tool output reaches
the agent. The container backend, real network namespaces, and filesystem
quotas remain documented follow-ons.

## M14 — Execution Isolation

**Issues:** [#38](https://github.com/deghosal-2026/hiveplane/issues/38) · [#39](https://github.com/deghosal-2026/hiveplane/issues/39)

- [x] [#38](https://github.com/deghosal-2026/hiveplane/issues/38) — Sandbox execution context
- [x] [#39](https://github.com/deghosal-2026/hiveplane/issues/39) — Resource caps and restricted egress

**Done when:** a destructive run executes without reaching control-plane resources; sandboxes are destroyed after the run; a run exceeding a cap is terminated and reported; disallowed egress is blocked.

## M15 — Tool-Output Shaping & Injection Scanning

**Issues:** [#40](https://github.com/deghosal-2026/hiveplane/issues/40) · [#41](https://github.com/deghosal-2026/hiveplane/issues/41)

- [x] [#40](https://github.com/deghosal-2026/hiveplane/issues/40) — Tool-output shaping pipeline (filter, truncate, budget)
- [x] [#41](https://github.com/deghosal-2026/hiveplane/issues/41) — Injection scanning of tool outputs

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

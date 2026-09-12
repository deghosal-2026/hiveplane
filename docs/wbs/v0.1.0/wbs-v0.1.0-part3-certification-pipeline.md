# WBS v0.1.0 — Part 3: Certification Pipeline **(THE THESIS)**

**Milestones:** M5-M7 · **Issues:** #19-#26

## Goal

SWE-bench-style certification for production agents. Run a workload against a reproducible benchmark, sign an attestation, and gate production on it. This part is the reason HivePlane exists.

## M5 — Benchmark Runner

**Issues:** [#19](https://github.com/deghosal-2026/hiveplane/issues/19) · [#20](https://github.com/deghosal-2026/hiveplane/issues/20) · [#21](https://github.com/deghosal-2026/hiveplane/issues/21)

- [ ] [#19](https://github.com/deghosal-2026/hiveplane/issues/19) — Benchmark corpus format and schema
- [ ] [#20](https://github.com/deghosal-2026/hiveplane/issues/20) — Benchmark runner service (isolated, deterministic)
- [ ] [#21](https://github.com/deghosal-2026/hiveplane/issues/21) — Seeded benchmark corpus for the demo workload

**Done when:** a malformed corpus is rejected, the runner is deterministic (same result twice), and the seeded corpus certifies the demo workload.

## M6 — Certification Engine & Signed Attestation

**Issues:** [#22](https://github.com/deghosal-2026/hiveplane/issues/22) · [#23](https://github.com/deghosal-2026/hiveplane/issues/23) · [#24](https://github.com/deghosal-2026/hiveplane/issues/24)

- [ ] [#22](https://github.com/deghosal-2026/hiveplane/issues/22) — Certification threshold evaluation
- [ ] [#23](https://github.com/deghosal-2026/hiveplane/issues/23) — Signed attestation generation
- [ ] [#24](https://github.com/deghosal-2026/hiveplane/issues/24) — Attestation verification on read + model-identity binding

**Done when:** thresholds assign status deterministically; attestations verify with the public key; forged attestations are rejected; a model-swap attempt is blocked.

## M7 — Certification API & CLI

**Issues:** [#25](https://github.com/deghosal-2026/hiveplane/issues/25) · [#26](https://github.com/deghosal-2026/hiveplane/issues/26)

- [ ] [#25](https://github.com/deghosal-2026/hiveplane/issues/25) — Certification REST API
- [ ] [#26](https://github.com/deghosal-2026/hiveplane/issues/26) — Certification CLI (`certify`, `certs list|show|compare`)

**Done when:** a workload can be certified end-to-end from the CLI, the compare endpoint surfaces per-task regressions, and the attestation id is returned.

## Dependencies

- Part 1 (certification models)
- Part 2 (registry stores status + attestations)
- Part 7 (sandbox is used to run benchmarks in isolation) — coordinate, M5 can start with a process-level runner and swap in the sandbox

## Exit Gate (M5, M6, M7)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Certification pipeline design](../../design/certification-pipeline-design.md)
- [Execution sandbox design](../../design/execution-sandbox-design.md)
- [PRD 01 Why](../../prd/01-why.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

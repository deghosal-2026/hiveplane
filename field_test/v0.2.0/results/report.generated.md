# HivePlane v0.2.0 — Field Test Report (generated)

> Generated 2026-10-02T01:22:14.818967+00:00 from `field_test/v0.2.0/results/summary.json` and the committed evidence.
> **Scenarios: 2/36 passed, 2 failed.**
>
> This file is **generated**; the curated `FIELD_TEST_REPORT.md` is hand-edited from it.

## Scenario results

| # | Scenario | Gates | Result | Evidence |
|---|----------|-------|--------|----------|
| S1 | Certification + signed attestation; uncertified refused | 1, 25 | not run | `field_test/v0.2.0/results/S1-*/` |
| S2 | Promotion gate blocks a regression with a replayable diff | 2 | not run | `field_test/v0.2.0/results/S2-*/` |
| S3 | Regression diff classifies pass->fail | 2 | not run | `field_test/v0.2.0/results/S3-*/` |
| S4 | Drift -> auto-quarantine + notify | 1 | PASS | `field_test/v0.2.0/results/S4-*/` |
| S5 | Reinstatement after re-cert | 1 | FAIL | `field_test/v0.2.0/results/S5-*/` |
| S6 | Triggers from >=3 sources with dedup/cooldown | 3 | not run | `field_test/v0.2.0/results/S6-*/` |
| S7 | 3-node pipeline end-to-end with per-step gates | 11 | not run | `field_test/v0.2.0/results/S7-*/` |
| S8 | Canary 10% -> auto-promote | 12 | FAIL | `field_test/v0.2.0/results/S8-*/` |
| S9 | Shadow run (no delivery) + outcome diff | 12 | not run | `field_test/v0.2.0/results/S9-*/` |
| S10 | Agent-as-tool propagates budget/policy/cert | 31 | not run | `field_test/v0.2.0/results/S10-*/` |
| S11 | Injection blocked; repeated attempts quarantine | 4 | not run | `field_test/v0.2.0/results/S11-*/` |
| S12 | Context budget exceeded -> clean pause + accounting | 20 | not run | `field_test/v0.2.0/results/S12-*/` |
| S13 | Spend-velocity breach -> pause | 20 | not run | `field_test/v0.2.0/results/S13-*/` |
| S14 | Circuit breaker trips and recovers | 8, 14 | not run | `field_test/v0.2.0/results/S14-*/` |
| S15 | Egress denied and audited | 8 | not run | `field_test/v0.2.0/results/S15-*/` |
| S16 | Secret never leaks; cross-tenant ref rejected | 13 | not run | `field_test/v0.2.0/results/S16-*/` |
| S17 | RBAC viewer denial + per-tenant isolation + 429 | 6, 34 | not run | `field_test/v0.2.0/results/S17-*/` |
| S18 | Worker kill -> lease reassign | 19, 33 | not run | `field_test/v0.2.0/results/S18-*/` |
| S19 | Urgent preempts best-effort, attributed | 18 | not run | `field_test/v0.2.0/results/S19-*/` |
| S20 | Dead trigger replays from DLQ once | 14 | not run | `field_test/v0.2.0/results/S20-*/` |
| S21 | Showback + cost-per-completed-task | 9 | not run | `field_test/v0.2.0/results/S21-*/` |
| S22 | Cache hit + savings; re-cert invalidates | 21 | not run | `field_test/v0.2.0/results/S22-*/` |
| S23 | GitOps: delete -> deregister; threshold change -> re-cert | 17 | PASS | `field_test/v0.2.0/results/S23-*/` |
| S24 | Synthetic probe flags decay before drift trips | 22 | not run | `field_test/v0.2.0/results/S24-*/` |
| S25 | Public verify + kill switch + provenance + worker-token refusal | 25, 29, 30 | not run | `field_test/v0.2.0/results/S25-*/` |
| S26 | Run fan-out delivery is auditable and reaches the sink (regression) | 5 | not run | `field_test/v0.2.0/results/S26-*/` |
| S27 | Trigger-driven run executes to completion (regression) | 3 | not run | `field_test/v0.2.0/results/S27-*/` |
| S28 | Defense ordering: denied tool vs allowed tool + injection (regression) | 4, 8 | not run | `field_test/v0.2.0/results/S28-*/` |
| S29 | Run fan-out audit vs M51 notification audit are distinct (regression) | 5, 15 | not run | `field_test/v0.2.0/results/S29-*/` |
| S30 | Run fan-out failure path records an on_failed delivery (regression) | 5 | not run | `field_test/v0.2.0/results/S30-*/` |
| S31 | Pipeline retry starts exactly one child per attempt (regression) | 11 | not run | `field_test/v0.2.0/results/S31-*/` |
| H1 | The running API image matches the source revision |  | not run | `field_test/v0.2.0/results/H1-*/` |
| H2 | Execution-model contract: queued runs do not auto-execute |  | not run | `field_test/v0.2.0/results/H2-*/` |
| H3 | Concurrent run-event appends get unique sequences |  | not run | `field_test/v0.2.0/results/H3-*/` |
| H4 | Startup recovery tolerates an orphaned run |  | not run | `field_test/v0.2.0/results/H4-*/` |
| H5 | Quarantine -> reinstate full cycle while quarantined | 1 | not run | `field_test/v0.2.0/results/H5-*/` |

## Per-scenario detail

| Scenario | Result | Detail |
|----------|--------|--------|
| S1 | not run | — |
| S2 | not run | — |
| S3 | not run | — |
| S4 | PASS | seeded drift -> AUTO-quarantined (actor=drift-detector, severity=critical); production refused |
| S5 | FAIL | reinstate -> 409 |
| S6 | not run | — |
| S7 | not run | — |
| S8 | FAIL | reinstate support-agent -> 409 |
| S9 | not run | — |
| S10 | not run | — |
| S11 | not run | — |
| S12 | not run | — |
| S13 | not run | — |
| S14 | not run | — |
| S15 | not run | — |
| S16 | not run | — |
| S17 | not run | — |
| S18 | not run | — |
| S19 | not run | — |
| S20 | not run | — |
| S21 | not run | — |
| S22 | not run | — |
| S23 | PASS | add temp -> register; delete -> deregister; threshold change -> re-certified; converged |
| S24 | not run | — |
| S25 | not run | — |
| S26 | not run | — |
| S27 | not run | — |
| S28 | not run | — |
| S29 | not run | — |
| S30 | not run | — |
| S31 | not run | — |
| H1 | not run | — |
| H2 | not run | — |
| H3 | not run | — |
| H4 | not run | — |
| H5 | not run | — |

## Release-gate coverage (34 gates)

Every gate must name an asserting scenario; a gate with none is **not yet demonstrated** (never `load`/`review`).

| Gate | Demonstrated by |
|------|-----------------|
| 1. Drifting agent auto-quarantined, notified, reinstated after re-cert | S4 |
| 2. Promotion gate blocks a regression with a replayable diff | **not yet demonstrated** (attempted: S2, S3) |
| 3. Triggers fire from >=3 sources with dedup/cooldown | **not yet demonstrated** (attempted: S6, S27) |
| 4. Seeded injection blocked; repeated attempts quarantine | **not yet demonstrated** (attempted: S11, S28) |
| 5. Slack approvals + fan-out to >=3 channels; mobile approvals | **not yet demonstrated** (attempted: S26, S29, S30) |
| 6. Per-tenant budget/policy/key isolation; viewer cannot approve | **not yet demonstrated** (attempted: S17) |
| 7. Helm chart deploys the full stack to a k3d cluster | **not yet demonstrated** |
| 8. Health dashboard, burn-through throttle, breaker trips/recovers | **not yet demonstrated** (attempted: S14, S15, S28) |
| 9. Showback by tenant -> team -> agent with cost-per-completed-task | **not yet demonstrated** (attempted: S21) |
| 10. Frame-by-frame replay + run diff; forked run re-runs edited state | **not yet demonstrated** |
| 11. Pipeline runs a multi-agent DAG end-to-end with per-step gates | **not yet demonstrated** (attempted: S7, S31) |
| 12. Canary routes 10% and auto-promotes on clean results | **not yet demonstrated** (attempted: S8, S9) |
| 13. A secret never appears in logs/traces/agent context | **not yet demonstrated** (attempted: S16) |
| 14. Dead trigger replays from DLQ; breaker trips/recovers | **not yet demonstrated** (attempted: S14, S20) |
| 15. SDK + API v2 round-trip; a plugin hook fires | **not yet demonstrated** (attempted: S29) |
| 16. Weekly digest auto-generates; artifact stored, linked, retained | **not yet demonstrated** |
| 17. Git deletes an agent -> deregister; threshold change -> re-cert | S23 |
| 18. Urgent run preempts best-effort with attribution | **not yet demonstrated** (attempted: S19) |
| 19. Worker on a second host; kill -> lease expiry reassigns | **not yet demonstrated** (attempted: S18) |
| 20. Context budget exceeded -> clean pause with accounting | **not yet demonstrated** (attempted: S12, S13) |
| 21. Cache hit reuses result + shows savings; re-cert invalidates | **not yet demonstrated** (attempted: S22) |
| 22. Synthetic probe flags decay before drift threshold | **not yet demonstrated** (attempted: S24) |
| 23. `ask` answers 5 live-state questions under budget + cert | **not yet demonstrated** |
| 24. Incident mode halts fleet in <5s and broadcasts | **not yet demonstrated** |
| 25. Attestation verifies publicly; kill switch disables a tool | **not yet demonstrated** (attempted: S1, S25) |
| 26. Signed image + SBOM; retention purge deletes tenant data | **not yet demonstrated** |
| 27. Operator-flagged failed run becomes a corpus case | **not yet demonstrated** |
| 28. Sampled production runs get judge scores; quality dip alerts | **not yet demonstrated** |
| 29. Modified bundle fails admission on provenance mismatch | **not yet demonstrated** (attempted: S25) |
| 30. Worker without a signed token is refused | **not yet demonstrated** (attempted: S25) |
| 31. Agent-as-tool calls propagate budget/policy/certification | **not yet demonstrated** (attempted: S10) |
| 32. Second controller replica does not double-reconcile | **not yet demonstrated** |
| 33. Chaos drills recover/halt (kill worker; revoke cert) | **not yet demonstrated** (attempted: S18) |
| 34. Per-workload service endpoint serves a run through all gates; 429s | **not yet demonstrated** (attempted: S17) |

## Evidence index

- **S1** — `api.log`, `certifications.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`, `workloads_used.json`
- **S2** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `response.json`, `run.log`
- **S3** — `api.log`, `diff.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S4** — `api.log`, `drift.json`, `notes.md`, `probe.json`, `probe.md`, `quarantine.json`, `requests.json`, `requests.log`, `run.log`
- **S5** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `reinstate.json`, `requests.json`, `requests.log`, `run.log`
- **S6** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`, `triggers.json`
- **S7** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`, `timeline.json`
- **S8** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S9** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `report.json`, `requests.json`, `requests.log`, `run.log`, `shadow.json`
- **S10** — `api.log`, `invocation.json`, `nested_run.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S11** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `quarantine.json`, `requests.json`, `requests.log`, `run.log`, `tool_call_0.json`, `tool_call_1.json`, `tool_call_2.json`
- **S12** — `api.log`, `context.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S13** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`, `velocity.json`
- **S14** — `api.log`, `denied.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S15** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`, `tool_call.json`
- **S16** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S17** — `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S18** — `api.log`, `drill.json`, `expiry.json`, `notes.md`, `probe.json`, `probe.md`, `reassign.json`, `requests.json`, `requests.log`, `run.log`
- **S19** — `api.log`, `notes.md`, `preemption.json`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S20** — `api.log`, `dlq.json`, `fire.json`, `notes.md`, `probe.json`, `probe.md`, `replay.json`, `requests.json`, `requests.log`, `run.log`
- **S21** — `api.log`, `budget.json`, `cost.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S22** — `api.log`, `cache.json`, `invalidation.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S23** — `api.log`, `apply-add.json`, `apply-delete.json`, `apply-threshold.json`, `notes.md`, `probe.json`, `probe.md`, `replan.json`, `requests.json`, `requests.log`, `run.log`
- **S24** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `probes.json`, `requests.json`, `requests.log`, `run.log`
- **S25** — `api.log`, `kill_switch.json`, `notes.md`, `probe.json`, `probe.md`, `provenance.json`, `requests.json`, `requests.log`, `run.log`
- **S26** — `api.log`, `deliveries.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S27** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.json`, `run.log`
- **S28** — `api.log`, `notes.md`, `ordering.json`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S29** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`, `surfaces.json`
- **S30** — `api.log`, `deliveries.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **S31** — `api.log`, `children.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`, `timeline.json`
- **H1** — `image.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **H2** — `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.json`, `run.log`
- **H3** — `api.log`, `events.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **H4** — `after_restart.json`, `api.log`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`
- **H5** — `api.log`, `cycle.json`, `notes.md`, `probe.json`, `probe.md`, `requests.json`, `requests.log`, `run.log`

## See also

- [docker-test-plan.md](docker-test-plan.md) - container layer (L0-L13)
- [DOCKER_TEST_REPORT.md](DOCKER_TEST_REPORT.md) - container-layer results
- `field_test/v0.2.0/results/` - raw per-scenario evidence (incl. `load/summary.json`)
- Curated narrative: [FIELD_TEST_REPORT.md](FIELD_TEST_REPORT.md)

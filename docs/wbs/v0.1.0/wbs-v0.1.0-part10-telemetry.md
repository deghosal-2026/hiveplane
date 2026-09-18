# WBS v0.1.0 — Part 10: Telemetry & Observability

**Milestones:** M19-M20 · **Issues:** #48-#51

## Goal

Correlate traces, metrics, logs, and audit events by run; expose fleet, certification, and cost signals; make a run's execution story readable.

## M19 — OTel Pipeline

**Issues:** [#48](https://github.com/deghosal-2026/hiveplane/issues/48) · [#49](https://github.com/deghosal-2026/hiveplane/issues/49)

- [x] [#48](https://github.com/deghosal-2026/hiveplane/issues/48) — Instrument API and adapters with OpenTelemetry
- [x] [#49](https://github.com/deghosal-2026/hiveplane/issues/49) — OTel Collector wiring (Tempo + Prometheus)

**Done when:** one run produces a complete trace linked from API call to adapter execution; traces appear in Tempo and metrics in Prometheus/Grafana with no manual setup.

## M20 — Fleet, Certification & Debug Signals

**Issues:** [#50](https://github.com/deghosal-2026/hiveplane/issues/50) · [#51](https://github.com/deghosal-2026/hiveplane/issues/51)

- [ ] [#50](https://github.com/deghosal-2026/hiveplane/issues/50) — Core fleet metrics
- [ ] [#51](https://github.com/deghosal-2026/hiveplane/issues/51) — Certification metrics and trace-linked debug context

**Done when:** runs by state/team, budget burn, failures, escalations, and intervention latency are graphed; certification pass rate, drift detections, attestation verification, and model-swap blocks are visible; opening a run shows its execution story.

## Dependencies

- Part 4 (run lifecycle)
- Part 9 (state store)

## Exit Gate (M19, M20)

- [ ] All tests in the system pass: `pytest`
- [ ] Code coverage total > 95%
- [ ] Ruff clean
- [ ] Mypy strict clean
- [ ] Update all relevant docs affected by this milestone
- [ ] Verify all issues in this milestone are done
- [ ] Close all completed issues
- [ ] Commit and push changes

## See Also

- [Telemetry design](../../design/telemetry-design.md)
- [Observability](../../observability.md)
- [v0.1.0 index](wbs-v0.1.0-index.md)

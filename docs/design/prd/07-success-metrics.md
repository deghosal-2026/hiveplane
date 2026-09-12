# PRD 07: Success Metrics

## TLDR

Product metrics measure operability; OSS metrics measure traction. Release gates define what "done" means for each version.

## Product Metrics

| Metric | Why it matters | Target (v0.1.0) |
|--------|----------------|-----------------|
| Agents onboarded | Proves the registry is usable | 3 real agents |
| Median time to inspect and stop a bad run | The core intervention loop | < 2 minutes |
| Percentage of runs with complete audit trail | Governance completeness | 100% |
| Budget overruns caught before manual discovery | Budget enforcement works | ≥ 1 in field test |

## OSS Metrics

- GitHub stars and issues from real platform users
- external experiments or adoption writeups
- article engagement and discussion quality

## Release Gates

### v0.1.0

- [ ] Three real agents run through the same lifecycle
- [ ] Operators can inspect and stop any run from one surface
- [ ] Budget enforcement demonstrably blocks an over-budget run
- [ ] Audit trail complete for every run in the field test
- [ ] Docker Compose stack starts with one command

### Later Versions

Release gates for v0.2.0+ to be defined alongside each WBS.

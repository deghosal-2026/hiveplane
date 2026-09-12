# PRD 07: Success Metrics

## TLDR

Product metrics measure operability and trust. Certification metrics measure whether agents earn and keep the right to operate. OSS metrics measure traction. Release gates define what "done" means for each version.

## Certification Metrics (THE PRIMARY SIGNAL)

| Metric | Why it matters | Target (v0.1.0) |
|--------|----------------|-----------------|
| Agents certified for production | Proves the certification pipeline works end-to-end | ≥ 1 |
| Certification pass rate on first attempt | Measures benchmark quality and agent readiness | measured, no target |
| Time from registration to certification | The onboarding friction | < 10 minutes |
| Regressions caught by re-certification | Proves the promotion gate works | ≥ 1 seeded |
| Drift detections (auto-quarantine) | Proves drift detection works | ≥ 1 seeded |
| False quarantine rate | Drift detector isn't over-triggering | 0 in field test |
| Attestation verification on every production admission | Integrity guarantee | 100% |
| Model-swap blocks | Model binding works | ≥ 1 seeded |

## Product Metrics

| Metric | Why it matters | Target (v0.1.0) |
|--------|----------------|-----------------|
| Agents onboarded | Proves the registry is usable | 3 real agents |
| Median time to inspect and stop a bad run | The core intervention loop | < 2 minutes |
| Percentage of runs with complete audit trail | Governance completeness | 100% |
| Budget overruns caught before manual discovery | Budget enforcement works | ≥ 1 in field test |
| Triggers firing correctly | Event-driven operation works | ≥ 1 alert-triggered run |
| Tool-output shaping prevents context overflow | Output governance works | ≥ 1 shaped run |
| Result fan-out delivered | Delivery works | ≥ 1 fanned-out result |

## OSS Metrics

- GitHub stars and issues from real platform users
- external experiments or adoption writeups
- article engagement and discussion quality
- **benchmark corpus contributions** from the community (long-term signal of ecosystem value)

## Release Gates

### v0.1.0

- [ ] Three real agents registered
- [ ] **At least one agent certified for production via benchmark**
- [ ] **An uncertified agent is refused admission to a production context**
- [ ] **A seeded manifest change is blocked by re-certification (regression caught)**
- [ ] Operators can inspect and stop any run from one surface
- [ ] Budget enforcement demonstrably blocks an over-budget run
- [ ] Execution isolation demonstrably caps a destructive run
- [ ] Tool-output shaping demonstrably truncates a large payload
- [ ] Audit trail complete for every run in the field test
- [ ] **Attestation is signed and verified on read**
- [ ] Docker Compose stack starts with one command
- [ ] `hiveplane init` scaffolds a working project in < 5 minutes

### v0.2.0

- [ ] **Drift detector auto-quarantines a seeded drifting agent**
- [ ] **Promotion gate blocks a regression and produces a replayable diff**
- [ ] Trigger rules fire from at least 2 sources (alert + cron or PR)
- [ ] Result fan-out delivers to at least 2 channels (Slack + webhook)
- [ ] Cost showback attributes spend by team and agent
- [ ] Context-aware policy differentiates staging vs. production for the same tool

### v0.3.0

- [ ] Agent health dashboard shows readiness, failure rate, SLO, drift
- [ ] Prompt-injection defense blocks a seeded injection via tool output
- [ ] Multi-runtime support (≥ 3 adapter types)

### v0.4.0

- [ ] Multi-tenant isolation verified
- [ ] Helm chart deploys to a reference cluster
- [ ] ROI dashboard shows fleet-wide spend vs. outcome

## See Also

- [Features](05-features.md)
- [Risks](08-risks.md)
- [Roadmap](09-roadmap.md)

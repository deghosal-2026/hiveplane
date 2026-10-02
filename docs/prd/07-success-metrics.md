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
| Attestation signing keypair persists across restarts | Integrity guarantee survives a process restart | 100% (file-backed key) |
| Model-swap blocks | Model binding works | ≥ 1 seeded |
| Certification executes the real agent entrypoint | The benchmark is real, not theater | 3/3 workloads |

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
| LLM provider seam exercised end-to-end | Real model calls flow through the boundary | ≥ 1 real model call |
| Agents receive real tool data | Agents reason over executed tool results, not fabricated outputs | 100% of tool calls |
| Durable resume | A paused run survives a process restart | pass |

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
- [ ] **Attestation signing key persists across restarts (prior attestations still verify)**
- [ ] **A real model call flows through `WorkerContext.complete()` (local or cloud provider)**
- [ ] **Agents receive real (fixture-backed) tool data, not fabricated outputs**
- [ ] **Certification executes the real agent entrypoint against the corpus**
- [ ] **A paused run survives a process restart and resumes correctly**
- [ ] **A fresh database migrates automatically on startup**
- [ ] **An escalated tool call is re-dispatched after approval**
- [ ] Docker Compose stack starts with one command
- [ ] `hiveplane init` scaffolds a working project in < 5 minutes

### v0.2.0 — The Complete Fleet OS (final big release; absorbs former v0.3.0/v0.4.0)

- [ ] **Drift detector auto-quarantines a seeded drifting agent, notifies the team, and reinstates it after re-cert**
- [ ] **Promotion gate blocks a regression and produces a replayable diff**
- [ ] Trigger rules fire from ≥3 sources (webhook + PR + alert/cron) with dedup/cooldown proven
- [ ] Fan-out delivers to ≥3 channels; Slack interactive approvals + mobile approvals work
- [ ] Cost showback attributes spend by tenant → team → agent with cost-per-completed-task
- [ ] Context-aware policy differentiates staging vs. production for the same tool
- [ ] Seeded prompt-injection via tool output is blocked; repeated attempts quarantine the agent
- [ ] Health dashboard shows readiness, failure rate, SLO burn; burn-through throttles; a circuit breaker trips and recovers
- [ ] Helm chart deploys the full stack to a k3d cluster
- [ ] Multi-tenant isolation verified (per-tenant budgets, policies, keys); viewer role cannot approve
- [ ] ROI dashboard shows fleet-wide spend vs. outcome
- [ ] A pipeline runs a multi-agent DAG end-to-end with per-step gates
- [ ] Canary routes 10% to a candidate and auto-promotes on clean results
- [ ] A secret never appears in logs/traces/agent context (verified by test)
- [ ] A dead trigger replays from the DLQ
- [ ] SDK + API v2 round-trip a full run; a plugin hook fires
- [ ] Weekly digest auto-generates; an artifact is stored, linked, and retained per policy
- [ ] Git deletes an agent → the plane deregisters it; git changes a cert threshold → re-cert fires (reconciliation)
- [ ] An urgent run preempts a best-effort run with attribution
- [ ] A worker daemon executes a run on a second host; kill it → lease expiry reassigns the run
- [ ] Context budget exceeded → run pauses cleanly with accounting shown
- [ ] A cache hit reuses a result and shows savings; re-cert invalidates it
- [ ] A synthetic probe flags decay before the drift threshold trips
- [ ] `ask` answers 5 live-state questions, itself under budget + cert
- [ ] Incident mode halts the fleet in <5s and broadcasts
- [ ] An attestation verifies publicly by ID; the tool kill switch disables a tool fleet-wide instantly
- [ ] Signed image + SBOM published with the release; retention purge deletes tenant data on schedule
- [ ] Field test: 25 scenarios + load test sustaining ≥50 concurrent runs
- [ ] An operator-flagged failed run becomes a corpus case included in the next certification
- [ ] Sampled production runs receive judge scores; a quality dip alerts before scheduled re-cert
- [ ] A modified agent bundle fails admission on provenance-signature mismatch
- [ ] A worker without a signed token is refused
- [ ] Agent-as-tool calls propagate budget/policy/certification to the nested run
- [ ] A second controller replica does not double-reconcile (leader election verified)
- [ ] Chaos drills: kill a worker mid-run → lease reassigns the run; revoke a cert mid-flight → run halts
- [ ] A per-workload service endpoint serves a run through all gates; over-limit tenants get 429s

## See Also

- [Features](05-features.md)
- [Risks](08-risks.md)
- [Roadmap](09-roadmap.md)

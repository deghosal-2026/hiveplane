# PRD 06: Security Baseline

## TLDR

HivePlane concentrates authority: it decides which agents may call which tools, how much they may spend, and when a human must approve. That makes it a high-value target and demands a security baseline from the first release.

## Threats

| # | Threat | Mitigation |
|---|--------|------------|
| T1 | Unauthorized task submission | Authenticated API; per-caller identity and scopes |
| T2 | Privilege escalation via tool permissions | Policy engine evaluates at the boundary; deny by default |
| T3 | Runaway spend | Hard budget limits enforced per run and per day |
| T4 | Audit tampering | Append-only, tamper-evident audit log |
| T5 | Adapter escape (runtime bypasses policy) | Adapters must route tool calls through the policy boundary; conformance tests |
| T6 | Secret leakage in telemetry/logs | Redaction before persistence; secrets never in run payloads |
| T7 | Operator action repudiation | Every intervention is attributed and auditable |

## Baseline Requirements

- deny-by-default tool policy
- authenticated and attributable operator actions
- budgets enforced before expensive work, not after
- no secrets in logs, traces, or audit events
- least-privilege credentials for adapters and the state store

## Adversarial Coverage

An adversarial suite should attempt: policy bypass via crafted task payloads, budget-evasion, approval skipping, audit-log tampering, and adapter conformance violations. Details land with the v0.1.0 WBS (Part 4 and Part 10).

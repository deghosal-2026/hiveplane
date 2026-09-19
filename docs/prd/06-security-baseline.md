# PRD 06: Security Baseline

## TLDR

HivePlane concentrates authority: it decides which agents may call which tools, how much they may spend, when a human must approve, and **whether an agent is certified to touch production at all**. That makes it a high-value target and demands a security baseline from the first release. Certification is itself a security control — an uncertified agent is an untrusted agent.

## Threat Model

| # | Threat | Mitigation |
|---|--------|------------|
| T1 | Unauthorized task submission | Authenticated API; per-caller identity and scopes |
| T2 | Privilege escalation via tool permissions | Policy engine evaluates at the boundary; deny by default |
| T3 | Runaway spend | Hard budget limits enforced per run and per day |
| T4 | Audit tampering | Append-only, tamper-evident audit log |
| T5 | Adapter escape (runtime bypasses policy) | Adapters must route tool calls through the policy boundary; conformance tests |
| T6 | Secret leakage in telemetry/logs | Redaction before persistence; secrets never in run payloads |
| T7 | Operator action repudiation | Every intervention is attributed and auditable |
| T8 | **Uncertified agent reaches production** | Registry enforces certification status at admission; production contexts require `certified` status; the gate is in the control plane, not in the agent |
| T9 | **Certification forgery** | Attestations are signed (key-paired) with a **persistent** signing key; verification on read; immutable storage. An ephemeral per-boot keypair would invalidate all prior attestations on restart |
| T10 | **Benchmark poisoning** — a crafted corpus makes a bad agent pass | Benchmark corpus is reviewed and versioned; corpus changes require approval; production threshold is separate from staging threshold; the benchmark executes the real agent, so a passing check reflects real behavior |
| T11 | **Model swap attack** — agent certified on model A, runs on model B | Certification binds to model identity; the **runtime model is reported by the provider from actual inference** and checked against the attestation; mismatch blocks the run and records a security event. Self-reported caller identity is not trusted |
| T12 | **Drift evasion** — agent decays slowly enough to avoid detection | Drift detector runs on a schedule AND on trigger (spike in failures escalates an immediate re-cert) |
| T13 | **Sandbox escape** — destructive run breaks out of isolation | Sandbox is a separate execution context with resource caps; network egress restricted; no shared filesystem with the control plane |
| T14 | **Prompt injection via tool output** — a malicious tool response hijacks the agent | Tool-output shaping layer inspects and bounds outputs; injection scanner at the boundary; suspicious patterns escalate for approval |

## Baseline Requirements

- deny-by-default tool policy
- authenticated and attributable operator actions
- budgets enforced before expensive work, not after
- no secrets in logs, traces, or audit events
- least-privilege credentials for adapters and the state store
- **production admission requires valid, unexpired certification**
- **attestations are signed and verified on every read, using a persistent signing keypair**
- **certification binds to model identity; the runtime model is reported from actual inference and a mismatch blocks the run**
- **all model calls route through the control-plane boundary; agents cannot call providers directly**
- **all tool calls execute through the boundary; agents cannot fabricate tool outputs**
- **sandbox network egress restricted; no shared filesystem with control plane**
- **tool outputs scanned for injection before reaching agent context**

## Adversarial Coverage

An adversarial suite should attempt:

- policy bypass via crafted task payloads
- budget-evasion (splitting work to stay under per-run limits)
- approval skipping
- audit-log tampering
- adapter conformance violations
- **certification forgery** (unsigned or tampered attestation)
- **benchmark poisoning** (crafted corpus that passes a bad agent)
- **model swap** (certified on A, running on B)
- **drift evasion** (slow decay below re-cert interval)
- **sandbox escape** (destructive run attempting to reach control-plane resources)
- **prompt injection via tool output**

Details land with the v0.1.0 WBS (Part 4, Part 6, and Part 10).

## Certification as a Security Control

Certification is not just a quality gate — it is a security control:

| Security property | How certification enforces it |
|-------------------|------------------------------|
| Trust | An agent must prove itself before touching production |
| Integrity | Attestation binds to model, benchmark version, and environment |
| Recency | Certifications expire; drift detection forces re-cert |
| Accountability | Every certification is signed and auditable |
| Blast radius | An uncertified or quarantined agent cannot reach production tools |

## See Also

- [Why](01-why.md)
- [Features](05-features.md)
- [Risks](08-risks.md)

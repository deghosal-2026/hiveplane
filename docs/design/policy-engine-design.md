# D4: Policy Engine Design

> Status: draft
> **v0.2.0:** extended by [Defense & Policy v2 (D29)](defense-policy-v2-design.md).

## Problem

Tool permissions, approval requirements, and escalation must be enforced consistently at the control-plane boundary — visible in config, not buried in agent code (DD-03). After the PRD rewrite, the policy engine must also handle: context-aware policy (staging vs. production, public vs. PII, blast-radius scoring), team policy packs (versioned, distributable), MCP tool trust levels (read-only vs. destructive), prompt-injection defense (input scanning, deterministic blocking), and certification status as a policy input.

See [PRD 02: Architecture](../prd/02-architecture.md) § Policy Engine and [PRD 05: Features](../prd/05-features.md) § Policy & Governance.

## Inputs

| Input | Source | Purpose |
|-------|--------|---------|
| Workload manifest (`spec.tools`, `spec.approvals`) | [Registry](registry-service-design.md) | Base tool permissions and approval requirements |
| Run context (agent, team, environment, action class) | Execution API | Context for context-aware policy |
| Budget state | [Budget service](budget-enforcement-design.md) | Whether budget allows the action |
| Certification status | Registry | `uncertified`, `provisional`, `certified`, `quarantined` — affects what actions are permitted |
| Tool trust level | MCP Tool Registry | `read_only` or `destructive` — affects approval requirements |
| Data sensitivity | Run context / task payload | `public`, `internal`, `pii`, `restricted` — affects tool permissions |
| Blast-radius score | Computed from action class, target environment, and tool trust level | High blast radius → escalation required |
| Team policy pack | Registry (versioned) | Team-wide policy overrides and defaults |
| Tool output (post-shaping) | [Runtime adapter](runtime-adapter-design.md) | Scanned for prompt-injection patterns |

## Decisions

| Decision | Meaning |
|----------|---------|
| `allow` | Proceed |
| `deny` | Block, record reason and originating rule |
| `escalate` | Pause for human approval; attach evidence (trace link, blast-radius score, tool output) |
| `block_injection` | Deterministically block a tool output that contains injection patterns; record as security event |

## Context-Aware Policy

The same tool may be allowed in one context and gated in another. Policy evaluation considers:

### Environment Context

| Environment | Behavior |
|-------------|----------|
| `sandbox` | Most tools allowed; destructive actions still require sandbox execution |
| `staging` | Read-only tools allowed; destructive tools require approval |
| `production` | Read-only tools allowed if certified; destructive tools require approval + certification + sandbox |

### Data Sensitivity

| Sensitivity | Behavior |
|-------------|----------|
| `public` | Standard permissions |
| `internal` | Standard permissions; outputs shaped |
| `pii` | Destructive tools denied; outputs redacted/masked |
| `restricted` | All destructive tools denied; read-only tools require approval |

### Blast-Radius Scoring

A blast-radius score is computed for each tool call request:

| Factor | Weight |
|--------|--------|
| Tool trust level (`destructive` > `read_only`) | High |
| Target environment (`production` > `staging` > `sandbox`) | High |
| Data sensitivity (`restricted` > `pii` > `internal` > `public`) | Medium |
| Action class (`production_write` > `destructive` > `read_only`) | High |
| Workload certification status (`uncertified` > `provisional` > `certified`) | Medium |

| Score Range | Decision |
|-------------|----------|
| Low (0–30) | `allow` (if not explicitly denied) |
| Medium (31–70) | `escalate` (approval required) |
| High (71–100) | `deny` (unless explicitly allowed with production certification) |

## Team Policy Packs

Team policy packs are versioned, distributable policy bundles applied across a team's workloads (PRD 05: policy & governance):

```yaml
apiVersion: hiveplane/v1
kind: PolicyPack
metadata:
  name: platform-team-default
  team: platform
  version: "2.1.0"
spec:
  defaults:
    sandbox_for_destructive: true
    output_shaping:
      injection_scan: true
      max_bytes: 16384
  overrides:
    - match:
        environment: production
        data_sensitivity: pii
      rules:
        - deny: destructive
        - require_approval: read_only
  approval_contacts:
    default: "#platform-oncall"
    pii: "#security-oncall"
```

Policy packs are applied in addition to the workload manifest. Pack rules can tighten permissions but never loosen them (manifest deny always wins).

### Pack Resolution Order

1. System defaults (deny-by-default)
2. Team policy pack rules
3. Workload manifest rules
4. Explicit deny wins over explicit allow at every level

## MCP Tool Trust Levels

The policy engine enforces tool trust levels from the MCP Tool Registry (PRD 05: tools & MCP):

| Trust Level | Behavior |
|-------------|----------|
| `read_only` | Allowed in most contexts (subject to data sensitivity and blast radius) |
| `destructive` | Requires approval in staging/production; requires sandbox; requires certification for production |

If a tool is not in the registry or not referenced in the manifest, it is denied by default.

## Prompt-Injection Defense

The policy engine includes an injection-scanning layer (PRD 05: policy & governance, T14):

### Input Scanning

Tool outputs (after shaping) are scanned for prompt-injection patterns before reaching the agent context:

| Pattern Category | Detection | Action |
|-----------------|-----------|--------|
| Instruction override | "ignore previous instructions", "you are now...", "system:" | `block_injection` |
| Tool-call hijack | Embedded tool-call syntax in output | `block_injection` |
| Data exfiltration | Requests to send data to external endpoints | `escalate` |
| Credential theft | Requests for secrets, tokens, API keys | `block_injection` |
| Role manipulation | "act as admin", "you have elevated permissions" | `block_injection` |

### Deterministic Blocking

High-confidence patterns are deterministically blocked — they do not reach the agent context and are recorded as security events. Lower-confidence patterns escalate for human review.

The injection scanner operates on shaped output (post-filter, post-truncation), so it sees the same content the agent would see.

## Certification Status as Policy Input

Certification status modifies policy decisions (DD-09, DD-15):

| Certification Status | Policy Effect |
|---------------------|---------------|
| `uncertified` | Sandbox only; no production tool access; no destructive actions |
| `provisional` | Staging only; read-only tools in staging; no production access |
| `certified` | Production access per manifest; destructive tools require approval + sandbox |
| `quarantined` | All runs blocked; workload is quarantined pending re-certification |

Certification is necessary but not sufficient (DD-15) — even `certified` agents are subject to budget, policy, sandbox, and approval enforcement at runtime.

## Evaluation Order

1. **Certification check** — if `quarantined`, deny all. If `uncertified` and target is production, deny.
2. **Injection scan** — if injection detected, `block_injection`.
3. **Explicit deny** — manifest or policy pack deny rule.
4. **Explicit allow** — manifest allow rule (subject to trust level and context).
5. **Tool trust level** — destructive tools require approval unless explicitly allowed with certification.
6. **Blast-radius scoring** — medium → escalate; high → deny.
7. **Action-class approval requirement** — if `required_for` matches, escalate.
8. **Default deny** — if no rule matched, deny.

Every decision returns:
- the decision (`allow`, `deny`, `escalate`, `block_injection`)
- the originating rule (manifest, policy pack, or system default)
- the reason (human-readable explanation)
- the blast-radius score (if computed)
- the certification status at decision time

## Explainability

Every decision returns a reason and the rule that produced it. Operators must be able to answer "why was this allowed/denied?" without reading code. The policy engine logs every decision with full context to the audit log and emits a metric.

## Open Questions

- policy expression language (structured config vs embedded rules)
- how policy packs are versioned and distributed by team
- whether blast-radius scoring weights are configurable per team
- injection scanner false-positive handling and tuning
- whether policy decisions are cached per run or re-evaluated per tool call

## See Also

- [Workload manifest](workload-manifest-design.md) — tool permissions, approval config, output shaping
- [Registry service](registry-service-design.md) — certification status, MCP tool registry, trust levels
- [Run lifecycle](run-lifecycle-design.md) — admission policy checks, escalation transitions
- [Runtime adapter](runtime-adapter-design.md) — injection scanning of tool outputs, output shaping layer
- [Budget enforcement](budget-enforcement-design.md) — budget state as policy input
- [Security baseline](../prd/06-security-baseline.md) — T2, T8, T14
- [Design decisions](design-decisions.md) — DD-03, DD-09, DD-13, DD-15

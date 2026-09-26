# D29: Defense & Policy v2 Design

> Status: draft

**Milestones:** M39–M40 · **Extends:** D4, D11

## Problem

D4 decides allow/deny/escalate from manifest rules, trust levels, and blast radius; D11 enforces only a tool-call-boundary egress string check. Neither is enough for an adversarial fleet: no deterministic injection scanner, no way to track untrusted data through a run, no real egress policy, no time awareness, no emergency off switch. One compromised tool output can reach a destructive tool before a human notices.

D29 supersedes D4 and D11 for v0.2.0. Their decision model and sandbox remain the substrate; D29 is authoritative on conflict.

## Overview

```
 input / tool output ─▶ Scanner ──block──▶ security event + audit
                          │ clean + taint
                          ▼
                    Taint Tracker ──provenance──┐
                                               ▼
 run context ──▶ Context-Aware Policy Engine (one code path)
 (env, sens, blast, budget, time, pack)        │ decision + rule_id + reason
                                               ▼
                                    Sandbox Boundary
                                    egress allow-list · kill switch
```

## Design

### Injection Defense

Deterministic scanners run at task-input admission and on tool output (post-shaping, pre-context). Detectors are pure functions — no model calls — so blocks are reproducible and auditable.

| Detector class | Examples | Default action |
|----------------|----------|----------------|
| Instruction override | "ignore previous instructions", "system:", "you are now" | `block_injection` |
| Instruction smuggling | encoded/segmented payloads, zero-width chars, homoglyphs, base64 blobs | `block_injection` |
| Tool-call hijack | embedded tool-call / JSON function syntax in untrusted output | `block_injection` |
| Exfiltration intent | "send … to https://", credential/token requests | `escalate` (high confidence → block) |
| Role manipulation | "act as admin", "elevated permissions" | `block_injection` |

Detectors are versioned (`detector_set_version`) and configurable per pack: `enabled`, severity threshold, benign-phrase allow-list. A block records `{detector_id, version, span, severity, decision, reason}` and emits a security event. False-positive controls: severity tiers, per-workload allow-lists, and a report-false-positive path that tunes a detector without disabling it fleet-wide.

### Taint Marks & Provenance

| Tag | Meaning |
|-----|---------|
| `trusted` | Authored by operator/manifest (task input, static config) |
| `untrusted` | Third-party tool output, fetched web content, trigger payload |
| `derived:<ids>` | Computed from upstream values; inherits the union of their trust |

Taint follows values through tool-call arguments. If any argument to a **destructive** tool is `untrusted` or `derived` from an untrusted value, policy denies by default unless the tool is explicitly allow-listed for untrusted input. Taint attaches to the run story and is advisory to policy, never a substitute. A blocked output never reaches context; the agent receives `injection_blocked` and the run continues where safe. Attempts are counted per workload over a rolling window; crossing `defense.repeat_threshold` (default 3) escalates to auto-quarantine through the shared immune machinery (D26) — the same path drift uses.

### Egress Allow-Lists

```yaml
spec:
  sandbox:
    network:
      egress: deny-by-default
      allow:
        - { host: api.openai.com, port: 443 }
        - { host: internal-metrics.corp.local, port: 443 }
```

Default-deny, enforced by the sandbox via netns/CNI (production) or the sandbox channel (dev). DNS is permitted only for allow-listed hosts. Every denial is audited and surfaced in the run story with the attempted host/port and the denying rule. Wildcards are explicit (`*.corp.local`) and lint-flagged.

### Context-Aware Policy Engine

All inputs are evaluated on **one code path**:

| Input | Source | Effect |
|-------|--------|--------|
| Environment | run context | staging vs production gating |
| Data sensitivity | manifest/task | `public` / `internal` / `pii` / `restricted` |
| Blast-radius score | computed | low → allow, medium → escalate, high → deny |
| Budget state | D5 | exhausted → deny/escalate |
| Time | policy clock | time-window rules |
| Certification | registry | status gates production |
| Taint | tracker | untrusted → destructive denied |

Every decision returns `{decision, reason, rule_id, pack_version, detector_set_version, blast_radius, certification, evaluated_at}`. `rule_id` pinpoints the originating rule (system default, pack, or manifest), so "why was this allowed?" is answerable without reading code.

**What-if / dry-run.** `POST /policy/evaluate` and `hiveplane policies check` return the decision the engine **would** make. What-if invokes the identical evaluator with `dry_run=true`; only side effects (audit write, approval creation) are suppressed. There is no second implementation, and a conformance test asserts what-if output equals the real decision for the same context.

### Team Policy Packs

Versioned, inheritable YAML bundles applied to a team's workloads. Resolution order: system defaults → inherited packs (parent → child) → workload manifest. Packs may tighten but never loosen (explicit deny wins at every level). `hiveplane policies lint` validates schema, references, wildcard egress, and inheritance cycles; `publish` writes an immutable version; `apply` pins a version to a team. The applied version is recorded on every decision and reconciled by GitOps (D22).

### Time-Window Policies

```yaml
spec:
  time_windows:
    - match: { action_class: destructive }
      allow: { days: [mon-fri], hours: "09:00-17:00", tz: America/New_York }
    - blackout:
        - { name: change-freeze, start: "2026-12-20T00:00Z", end: "2027-01-02T00:00Z" }
```

Destructive actions outside the window are denied with `outside_time_window`; blackout calendars deny all matched actions. Time is read from the policy clock (injectable for tests), never the agent's clock.

### Tool Kill Switch

The kill switch disables any tool fleet-wide near-instantly. It is a control-plane flag checked at the tool-call boundary **before** policy packs or cached decisions, so a stale cache cannot re-enable a killed tool. Disable and re-enable are audited with actor, tool ID, reason, and timestamp. State propagates on a dedicated fast channel and is fail-closed: if the plane cannot confirm a tool is enabled, it denies.

## Data Model

```
policy_decisions(id, run_id, tool_id, decision, reason, rule_id,
  pack_version, detector_set_version, context JSONB, dry_run, created_at)
security_events(id, run_id, workload_id, kind, detector_id,
  detector_version, detail JSONB, created_at)
  -- kind: injection | egress_denied | taint_block | repeated_attempt
tool_kill_switch(tool_id PK, disabled BOOL, reason, actor, changed_at)
```

## Interfaces / API

```
POST /policy/evaluate          # real or dry_run
GET  /policy/decisions?run_id=...
GET  /security/events?workload_id=...
POST /tools/{tool_id}/disable
POST /tools/{tool_id}/enable
```

CLI: `hiveplane policies lint|publish|apply`, `hiveplane policies check`, `hiveplane tools disable|enable`.

## Failure Modes

| Failure | Behavior |
|---------|----------|
| Scanner unavailable | fail-closed for destructive calls; read-only allowed and flagged |
| Detector false positive | block recorded and explainable; override requires operator action |
| Kill-switch channel down | boundary treats tool as disabled (fail-closed) |
| Policy pack fetch fails | last-good pinned version used; decisions record the pinned version |
| Taint provenance lost | value treated as `untrusted` |

## Security

- Defense is deterministic and boundary-enforced; nothing relies on agent cooperation.
- Every block, egress denial, taint block, and kill-switch change is an audit event (DD-07).
- Secrets never appear in scanner payloads, decision records, or security events (D33).
- Egress default-deny and kill-switch fail-closed are lint-enforced and non-negotiable.

## Testing

- Seeded injection via tool output is blocked with a reason and detector version.
- Taint propagates so an untrusted value cannot silently reach a destructive tool.
- Repeated attempts quarantine the workload via the shared machinery.
- Egress to a non-allow-listed host is denied and audited.
- Staging vs production decisions differ; what-if equals the real decision.
- Pack inheritance/lint works; kill switch disables a tool instantly and re-enables audited.

## Open Questions

- Should taint be tracked at value granularity or argument granularity (cost vs precision)?
- Are detector sets shared across tenants or per-tenant?
- How long are raw security-event spans retained vs. hashed?

## See Also

- [Policy Engine Design](policy-engine-design.md) (D4) — superseded for v0.2.0 behavior
- [Execution Sandbox Design](execution-sandbox-design.md) (D11) — egress enforcement substrate
- [Runtime Guards Design](runtime-guards-design.md) (D30) — guards as policy decisions
- [MCP Registry v2 Design](mcp-registry-v2-design.md) (D32) — trust levels and kill switch
- [Secrets, RBAC & Identity Design](secrets-rbac-design.md) (D33) — redaction, operator roles
- [PRD 05: Features](../prd/05-features.md) — Policy & Governance, Safe Execution
- [WBS Part 8](../wbs/v0.2.0/wbs-v0.2.0-part8-defense-policy.md) — M39–M40

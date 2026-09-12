# HivePlane v0.1.0 — Field Test Report

> Status: **pending** — populated after M18 execution.

## Summary

_To be completed._

## Environment

| Item | Value |
|------|-------|
| Date | — |
| HivePlane version | v0.1.0 |
| Control plane host | — |
| LLM providers exercised | — |
| Adapters exercised | raw-worker, langgraph |
| Docker Compose start | one command | — |

## Workloads

| Workload | Runtime | Certification Status | Runs | Completed | Failed | Escalated |
|----------|---------|---------------------|------|-----------|--------|-----------|
| repo-agent | raw-worker | — | — | — | — | — |
| docs-agent | langgraph | — | — | — | — | — |
| incident-agent | raw-worker | — | — | — | — | — |

## Certification Results

| Workload | Benchmark Corpus | Pass Rate | Critical Failures | p99 Latency (s) | Attestation Signed | Status |
|----------|-----------------|-----------|-------------------|-----------------|-------------------|--------|
| repo-agent | corpora/repo-agent/v1 | — | — | — | — | — |
| docs-agent | corpora/docs-agent/v1 | — | — | — | — | — |
| incident-agent | corpora/incident-agent/v1 | — | — | — | — | — |

## Certification Gate Tests

| Test | Result |
|------|--------|
| Uncertified agent refused production admission | — |
| Seeded manifest change blocked by re-certification (regression) | — |
| Attestation signed and verified on read | — |
| Model-swap blocked (certified on A, running on B) | — |
| Attestation verification on every production admission (100%) | — |

## Sandbox Results

| Test | Result |
|------|--------|
| Destructive run executed in isolated context | — |
| Memory cap enforced | — |
| CPU cap enforced | — |
| Wall-clock cap enforced | — |
| Output cap enforced | — |
| Egress restricted to allowlist | — |
| No shared filesystem with control plane | — |
| Tool calls routed through policy boundary in sandbox | — |

## Output Shaping Results

| Test | Result |
|------|--------|
| Large payload truncated per `max_bytes_per_tool_call` | — |
| Filter rules applied (secrets redacted, patterns matched) | — |
| Cumulative output budget enforced | — |
| Injection scanning detected suspicious pattern (seeded) | — |
| No raw tool output reached agent context | — |

## Results by Acceptance Criterion

| # | Criterion | Result |
|---|-----------|--------|
| A1 | Three real agents registered | — |
| A2 | At least one agent certified for production | — |
| A3 | Uncertified agent refused production admission | — |
| A4 | Seeded regression blocked by re-certification | — |
| A5 | Attestation signed and verified on read | — |
| A6 | Model-swap blocked | — |
| A7 | Agents through full lifecycle | — |
| A8 | Budget enforcement blocks over-budget run | — |
| A9 | Execution isolation caps destructive run | — |
| A10 | Tool-output shaping truncates large payload | — |
| A11 | Guarded tool call requires approval | — |
| A12 | Paused run survives restart | — |
| A13 | Operators can inspect and stop any run | — |
| A14 | Result fan-out delivered | — |
| A15 | Audit trail complete | — |
| A16 | Median time to inspect + stop a bad run | — |
| A17 | Docker Compose starts with one command | — |
| A18 | `hiveplane init` scaffolds in < 5 min | — |
| A19 | Certification dashboard renders | — |
| A20 | Spend view shows cost showback | — |

## Observability

| Metric | Value |
|--------|-------|
| Runs by state | — |
| Budget burn (total) | — |
| Budget burn by team | — |
| Budget burn by agent | — |
| Cost-per-completed-task | — |
| Failures | — |
| Escalations | — |
| Intervention latency (median) | — |
| Certification pass rate (first attempt) | — |
| Drift detections | — |
| False quarantine rate | — |
| Attestation verifications | — |
| Model-swap blocks | — |

## Fan-Out Delivery

| Workload | Destination | Trigger | Delivered | Trace Link | Attestation Link |
|----------|------------|---------|-----------|------------|-----------------|
| repo-agent | — | on_completion | — | — | — |
| docs-agent | — | on_completion | — | — | — |
| incident-agent | — | on_escalation | — | — | — |

## Learnings

_To be completed._

## Known Issues

_To be completed._

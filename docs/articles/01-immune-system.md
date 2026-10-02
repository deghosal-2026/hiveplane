# The Immune System for Agent Fleets

**Status:** draft · **Pillar:** certification, drift, quarantine, promotion gate

## Thesis

Agent frameworks ship orchestration; almost none ship *admission control*. HivePlane treats
an agent like a transplant: it must prove itself against a reproducible benchmark before it
runs in production, and it is re-certified — and can be auto-quarantined — when it drifts.
This is not a feature; it is the thesis.

## Audience

Platform engineers and SREs who already run one or two agents and feel the "who approved
this change?" problem. Also the skeptic who thinks evals are optional.

## Outline

1. **The failure mode.** A prompt or model swap ships quietly; production behavior changes;
   nobody notices until a customer does.
2. **Bind certification to the artifact.** The artifact hash covers the behavior-affecting
   spec, the toolset, the exact model identity from inference, and the policy version. Change
   any → the workload is automatically marked uncertified.
3. **The promotion gate.** Production admission requires a valid, unexpired attestation *for
   the current hash* plus a verified, signed agent bundle. A model swap is refused by name.
4. **Drift is measured against the agent's own baseline.** False-positive controls: one
   exceeding run is a warning; quarantine needs N consecutive failures or a strong signal.
5. **Quarantine and reinstatement.** Quarantine revokes admission immediately, notifies the
   owner, and requires a *fresh passing certification* to reinstate — no override.
6. **Transparency.** Every certification appends to a hash-chained log; anyone can verify an
   attestation publicly by id.

## Evidence to link

- Field test S4/S5 (`docs/field-test/v0.2.0/FIELD_TEST_REPORT.md`) — auto-quarantine → reinstate
- [Certification v2 design](../design/certification-v2-design.md) · [Learning loop](../design/learning-loop-design.md)
- [Tutorial 2 — Certify, Promote & Survive Drift](../tutorials/02-certify-promote-drift.md)
- [Migration guide](../release/v0.2.0/migration-guide.md) §3.7 (behavior changes)

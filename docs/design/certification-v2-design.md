# D26: Certification v2 Design

> Status: implemented (M32 promotion gate, M33 regression diff, M34 drift/quarantine/reinstatement, M35 transparency/public verification/workload provenance)

**Milestones:** M32–M35 · **Extends:** D10

## Problem
D10 certifies behavior at a point in time. It does not stop a changed manifest from reaching production silently, explain what regressed when re-certification fails, detect slow decay after certification, or prove the code in production is the code that was benchmarked. Attestations are also not independently verifiable. Certification v2 binds certification to an immutable artifact hash, gates staging→production promotion on that hash, produces an explainable regression diff, auto-quarantines on drift, expires stale certifications, publishes attestations to a transparency log, and binds certification to signed code identity.

## Overview
```
 config change ─▶ Promotion Gate ──hash match?──▶ production admission
                       │ no                              ▲
                       ▼                                 │ provenance verify
                re-cert required                         │ (signed bundle digest)
                       │ benchmark                       │
                       ▼                                 │
             Certification v2 ──append──▶ Transparency Log ──▶ public verify
                       │
                       ▼
                Drift Detector ──decay──▶ auto-quarantine ──▶ notify owner
                       ▲
                       └── fix + re-cert + promote ── reinstatement
```

## Design

### Artifact hash — the certification binding
Certification is bound to one `artifact_hash`; everything that can change behavior is an input. Under-hashing lets a change slip through; over-hashing causes false re-certs.
```python
artifact_hash = sha256(canonical_json({
    "manifest_hash":  sha256(manifest.canonical_json()),   # prompt, runtime, tools.allow, budget
    "toolset":        sorted([{"id": t.id, "version": t.version}
                              for t in manifest.tools.allow]),
    "model_binding":  model_identity,                       # exact ID, never an alias
    "policy_version": policy_pack.version,                  # team policy pack
}))
```
A new manifest version computes a new hash. If it differs from the hash on the current production attestation, the workload is marked **`uncertified` for production** — never silently passed (M32-04). The gate diffs the hash pre-image to name the exact changed binding(s).

### Promotion gate
Contexts are `staging` and `production`, each with its own thresholds (D10). Promotion is a request, not a mutation:
1. Operator requests promotion of manifest version `vN` to production.
2. Gate computes `artifact_hash(vN)` and looks for a valid, **unexpired**, `certified` attestation with that exact hash and `target_context=production`.
3. If absent → **refuse**, naming the changed binding(s) (`model_binding: gpt-4o-2024-08-06 → gpt-4o-2024-11-20`).
4. If present and the regression diff against the baseline is clean → **admit**.
5. Every attempt, refusal, and certification is audited and linked into the run/attestation story (DD-07, DD-11).
An uncertified workload cannot be promoted under any trigger trust level. The D10 override path remains explicit, named, reasoned, audited, visible, and rare.

### Regression diff
When re-certification completes, the engine diffs the candidate against a **baseline** — the last certified attestation by default, or an explicit attestation id.
- **Task status:** `pass→fail` (regression), `fail→pass` (improvement), unchanged.
- **Metric deltas:** pass rate, critical failures, p50/p95 latency, tokens, estimated cost.
- **Severity:** a `pass→fail` on a `critical` task is a **critical regression** and hard-blocks promotion; threshold-crossing deltas are **warnings**.
- **Replayable traces:** each regressed task links to its trace id and replay frame set (D18).
- **Artifacts:** machine-readable JSON and a human report attached to the certification record.
```
hiveplane certs compare <v1> <v2> [--json]
```
`--json` output is schema-validated and stable. Comparing identical certifications yields an empty diff; unchanged corpora produce no false positives.

### Drift detector & auto-quarantine
Each production workload has a re-certification cadence (cron, per workload). Drift is measured against the agent's **own** baseline (DD-12) with threshold and trend logic:
```
DRIFT if  delta_pass_rate > threshold            # e.g. 0.10
       or any critical task pass→fail
       or new_failures > max_new_failures
       or N consecutive re-certs below threshold # trend, not a single flaky run
```
On drift: suspend admission, revoke production admission immediately, cancel/pause in-flight work per policy, mark `quarantined`, and notify the owner (D15) with the regression diff and replay traces. Quarantine history and reason persist and appear on the certification dashboard.
**Reinstatement:** fix → re-certify → promote back → resume admission; all audited. **False-positive controls:** require N consecutive failures or a minimum delta before quarantining; a single flaky run never quarantines. **Expiry:** every attestation carries `expires_at = issued_at + renewal_window` (per policy); an **expired certification is not admissible**, even if never superseded. The scheduler re-certifies before expiry; a renewal creates a new attestation linked via `previous_attestation_id`, superseding — never mutating — the old one.

### Attestation transparency log
Every certification is appended to an append-only, hash-chained log:
```
entry_hash = sha256(prev_hash ‖ seq ‖ attestation_id ‖ canonical_json(attestation))
```
`seq` is monotonic and `prev_hash` links each entry to its predecessor, so any edit or deletion is detectable via inclusion and consistency proofs. The log is the canonical record for public verification.

### Public attestation verification
`GET /attestations/{id}/verify` is **unauthenticated** and returns validity, `signer_key_id`, `issued_at`, status, and a chain proof. It never returns workload internals, corpus contents, prompts, model inputs, or secrets — only public evidence.

### Workload provenance & signing
At registration the control plane signs an **agent bundle** = `manifest` + `adapter/entrypoint digest` (e.g. `sha256` of the entrypoint module/file). At admission and on export/import the signature and digest are verified against the attested identity; a tampered or swapped bundle **fails production admission**; exported/imported bundles carry signatures and a tampered import is refused. Keys persist across restarts, rotate by `key_id`, and old public keys are retained so **old attestations still verify after rotation**.

### Behavior certification + code identity
Behavior certification proves the agent passes the benchmark for an `artifact_hash` (declarative config + model + policy). Provenance proves the bytes about to execute match the signed bundle digest. Production admission requires **both**: a valid unexpired certification for the current artifact hash **and** a verified code identity. Neither alone suffices — correct code without certification is refused, and a certified configuration running swapped code is refused.

## Data Model
```
certification_bindings  id, workload_id, artifact_hash, manifest_version, toolset_hash, model_identity, policy_version, created_at
promotions              id, workload_id, artifact_hash, from_context, to_context, certification_id, status, refusal_reason, operator, timestamp
regression_diffs        id, base_attestation_id, candidate_attestation_id, severity, machine_report JSONB, human_report TEXT, created_at
quarantines             id, workload_id, reason, trigger, evidence_attestation_id, detected_at, reinstated_at, operator
attestation_log         seq PK, attestation_id, prev_hash, entry_hash, created_at
workload_bundles        id, workload_id, bundle_digest, signature, key_id, registered_at
signing_keys            key_id PK, public_key, status, created_at, retired_at
```

## Interfaces / API
```
POST  /promotions                      { workload_id, manifest_version, to_context }
  → 200 { promoted, certification_id } | 409 { refused, changed_bindings: [...] }
GET   /promotions/{id}                 → promotion record + audit links
GET   /certifications/compare/{v1}/{v2}?json=true   → 200 { regression_diff }
GET   /attestations/{id}/verify        → public, no auth: validity + chain proof
GET   /attestations/log/proof/{id}     → inclusion/consistency proof
POST  /workloads/{id}/bundle/verify    → { valid, digest, key_id }
```
```
hiveplane promote <workload> --to production
hiveplane certs compare <v1> <v2> [--json]
hiveplane verify <attestation_id>
hiveplane bundle sign|verify <workload>
```

## Failure Modes
| Condition | Behavior |
|-----------|----------|
| Artifact changed after certification | Hash mismatch → uncertified for production; promotion refused, changed binding named |
| Re-certification regression | Promotion blocked; diff + replay traces; critical regression hard-blocks |
| Drift below threshold | Auto-quarantine, admission revoked, owner notified |
| Single flaky re-cert failure | No quarantine unless N consecutive failures or minimum delta met |
| Certification expired | Not admissible; re-certify to renew |
| Bundle tampered / code swapped | Signature or digest mismatch → admission refused |
| Transparency log tampered / unknown id | Chain proof fails → invalid; unknown id → 404 with no information leak |
| Signing key rotated | New entries use new `key_id`; old attestations still verify with retained keys |

## Security
- Public verification returns **only** validity and public evidence — no prompts, inputs, corpus data, or secrets.
- Attestation signatures cover canonical JSON excluding the signature; verification is public.
- Hash-chained log makes silent history edits detectable.
- Provenance binds executable bytes, not just declared config; tampered bundles cannot reach production.
- Signing keys persist, rotate by `key_id`, and are distributed as public verification material; rotation never invalidates old attestations.
- Overrides remain explicit, audited, visible, and rate-monitored.

## Testing
- Changed manifest/toolset/model-binding/policy blocks promotion and names the changed binding; unchanged certified promotes.
- A seeded regression is pinpointed to exact task(s) with metric deltas; identical certifications diff empty.
- A seeded drifting agent is auto-quarantined and its owner notified; a stable agent is never falsely quarantined; reinstatement restores admission.
- An expired certification blocks production admission.
- A forged attestation is rejected; log tampering is detected; public verify works unauthenticated.
- A tampered bundle fails admission; tampered imports are refused; key rotation preserves old attestation validity.
## Open Questions

- Should `policy_version` be part of the artifact hash or an independent invalidation axis, and can partial re-certification satisfy the gate?
- How are inclusion proofs exposed without turning the public endpoint into a workload-enumeration oracle?
- Who owns workload signing keys — control plane, tenant, or an external KMS per tenancy?

## Implementation (M32)

| Module | Responsibility |
|--------|----------------|
| `hiveplane.certification.binding` | `ArtifactBinding` + `compute_binding` (manifest/toolset/model/policy sha256) and `changed_bindings` |
| `hiveplane.certification.promotion` | `PromotionGate` (admit/refuse on a certified artifact hash) and `recertify_and_promote` |
| `hiveplane.certification.promotion_store` | Promotion records (memory + Postgres, migration `0010`) |
| `hiveplane.api.promotions` | `POST /promotions`, `POST /promotions/recertify`, `GET /promotions[/{id}]` |
| `hiveplane.registry.service` | `artifact_hash`, `mark_uncertified_for_production`, automatic invalidation on change |

**Artifact hash.** `sha256` over canonical JSON of the behavior-affecting spec
fields, the sorted toolset, the exact canonical model identity, and the resolved
policy version. The certification block and identity metadata are excluded, so
applying an attestation never invalidates it and an owner change never forces a
re-cert.

**Promotion gate.** `hiveplane promote <workload> --to production` (or
`POST /promotions`) computes the target manifest's binding, requires the current
version to be `certified` with a valid unexpired production attestation for that
exact hash, and otherwise refuses — naming the changed binding(s) (e.g.
`model_binding: openai/gpt-4o/2024-08-06 -> openai/gpt-4o/2024-11-20`). An
uncertified workload can never be promoted. Every attempt is recorded and
audited (`promotion.admitted` / `promotion.refused`).

**Automatic invalidation.** When a manifest version changes the artifact hash of a
certified/provisional workload, the registry marks it `uncertified` and requires
re-certification, so a changed artifact cannot silently keep production
admission.

**Re-certification.** `POST /promotions/recertify` (or `hiveplane promote
--recertify`) runs the benchmark for the current artifact through the
certification coordinator, then attempts promotion.

M33–M35 add the regression diff, drift detector/auto-quarantine, transparency
log, public verification, and workload provenance/signing.

### Regression diff (M33)

`hiveplane.certification.diff` compares two benchmark results task by task:

- **Deltas** — `pass→fail` (regression) and `fail→pass` (improvement), with
  latency, token, and cost deltas per task.
- **Severity** — a `pass→fail` on a `critical` task is a **critical regression**
  (overall severity `critical`); other regressions are `warning`. Critical
  regressions feed the promotion gate's refusal reason.
- **Replayable traces** — every changed task carries a `ReplayFrameSet`
  (task contract: input, expected, check; plus the run's trace id and a stable
  `replay_ref`), so the exact task can be replayed deterministically.
- **Baseline selection** — `compare_to_baseline(workload, after_id)` uses the
  last `certified` record unless an explicit `baseline_id` is given; comparison
  is deterministic (identical results diff empty).
- **Artifact** — a `RegressionReport` (machine-readable JSON + human summary) is
  attached to the certification record when re-certification runs.

API/CLI: `GET /certifications/compare/{before}/{after}`,
`GET /certifications/compare-baseline/{after}?workload=...&baseline=...`; and
`hiveplane certs compare <v1> <v2> [--json]`,
`hiveplane certs compare-baseline <workload> <after> [--baseline <id>] [--json]`.

### Drift detector, auto-quarantine & reinstatement (M34)

`hiveplane.drift` watches certified workloads for behavioral decay and makes
quarantine reversible:

- **Scheduler** — per-workload re-certification cadence from the manifest's
  `certification.re_cert_interval` (fleet default fallback); `due()` is
  deterministic given the clock. Also exposes certification **expiry/renewal**
  states (`valid`/`expiring`/`expired`); expired attestations are not admissible
  (enforced by `RegistryService._has_valid_attestation`).
- **Detector** — compares a fresh `EvalSummary` to the certified baseline:
  pass-rate drop and new failures against `certification.drift_threshold_pass_rate`
  / `max_new_failures`. Severity is `critical` for critical failures or a strong
  (≥2× threshold) signal.
- **False-positive controls** — a single exceeding run is a `warning`; quarantine
  requires `drift.required_consecutive_failures` consecutive exceeding runs (or a
  strong signal). A stable agent is never quarantined.
- **Auto-quarantine** — `QuarantineService` forces status `quarantined` (which
  immediately revokes production admission), optionally cancels in-flight runs
  per `drift.cancel_in_flight`, persists a `QuarantineRecord` with reason and
  evidence, notifies the owner via Slack/webhook fan-out (D15), and audits
  `workload.quarantined`.
- **History & dashboard** — quarantine records and drift assessments persist
  (`quarantines`, `drift_assessments`) and appear on the certification dashboard
  with reason and severity.
- **Reinstatement** — `ReinstatementService` requires a *fresh* passing
  certification (staging recovery → production certification) and re-granted
  production admission before marking the quarantine `reinstated`; every step is
  audited (`workload.reinstated`).

API: `GET /drift/{due,schedules,expiries}`, `POST /drift/{assess,probe}`,
`GET|POST /quarantines`, `POST /quarantines/{id}/reinstate`.
CLI: `hiveplane drift {due,schedules,expiries,assess,probe,quarantine,quarantines,reinstate}`.

### Attestation transparency, public verification & provenance (M35)

`hiveplane.transparency` completes the trust story — code identity as well as
behavior, and independent verifiability:

- **Transparency log** — every certification appends one entry to an append-only,
  hash-chained log (`entry_hash = sha256(prev_hash ‖ seq ‖ attestation_id ‖
  canonical_json(attestation))`). `verify_chain()` recomputes the chain and
  reports the first inconsistency (broken link, reorder, deletion, or content
  mismatch); `prove(id)` returns inclusion evidence. Persisted in `attestation_log`
  (migration `0012`); in-memory by default.
- **Public verification** — `GET /attestations/{id}/verify` is unauthenticated and
  returns only public evidence (validity, `signer_key_id`, `issued_at`, status,
  chain position/validity). Unknown ids return `404` with no information leak; a
  forged or non-included attestation returns `valid: false`. CLI:
  `hiveplane verify <attestation_id>`.
- **Workload provenance** — at registration the control plane signs an agent
  bundle (manifest identity + entrypoint digest) via `sign_bundle`. Production
  admission now requires a valid unexpired certification **and** a verified
  bundle (`RegistryService._has_valid_bundle`); a tampered digest/signature or a
  swapped manifest is refused. Export/import envelopes carry the signature and
  `import_bundle` refuses tampered imports.
- **Key management** — `SigningKeyRegistry` distributes public keys by `key_id`
  and rotates them: the previous key is retired but retained, so attestations and
  bundles signed under old keys still verify. Persisted in `signing_keys`
  (migration `0013`).
- **Fan-out** — result notifications include a `verification_url` (from
  `certification.public_verification_base_url`).

API: `GET /attestations/{id}/verify`. CLI: `hiveplane verify`. When a control
plane is configured without a bundle signing key, provenance is not enforced and
certification alone governs admission (backward compatible).

## See Also
- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — runner, engine, thresholds, promotion gate, drift
- [Benchmark Execution Design](benchmark-execution-design.md) (D19) — real agent execution under benchmark
- [Corpus-Fixture Coupling Spec](corpus-fixture-coupling.md) (D20) — deterministic task contracts
- [Agent Health Design](agent-health-design.md) (D16) — drift signals → health · [Durable Resume Design](durable-resume-design.md) (D18) — replay frame sets
- [Result Fan-out Design](result-fanout-design.md) (D15) — quarantine/owner notifications
- [PRD 05: Features](../prd/05-features.md) — Certification Pipeline theme · [PRD 09: Roadmap](../prd/09-roadmap.md) — pillar C
- [WBS v0.2.0 Part 4](../wbs/v0.2.0/wbs-v0.2.0-part4-runtime-promotion.md) (M32), [Part 5](../wbs/v0.2.0/wbs-v0.2.0-part5-drift-quarantine.md) (M33–M34), [Part 6](../wbs/v0.2.0/wbs-v0.2.0-part6-provenance-learning.md) (M35)

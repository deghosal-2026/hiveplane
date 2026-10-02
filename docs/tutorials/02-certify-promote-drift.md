# Tutorial 2 — Certify, Promote & Survive Drift

The certification pipeline is HivePlane's thesis. In this tutorial you will bind a
certification to an artifact, watch a change invalidate it, promote only with evidence, and
survive a drift event end-to-end.

Prerequisite: [Tutorial 1](01-getting-started.md) (stack running, `repo-agent` registered).

## 1. What certification binds to

Certification produces an **artifact hash** over:

- the behavior-affecting manifest fields,
- the sorted toolset,
- the exact **model identity from actual inference**, and
- the resolved policy version.

Change any of these and the hash changes — so an applied attestation never invalidates
itself, but a real behavior change always does. Confirm the current state:

```bash
hiveplane certs list --workload repo-agent
hiveplane certs show <certification-id>          # note the artifact_hash and bindings
```

## 2. Promote with evidence

Promotion requires a valid, unexpired `certified` attestation for the *current* artifact
hash:

```bash
hiveplane promote repo-agent --to production
```

If it succeeds, production traffic may be admitted. If it is refused with `409`, the reason
names the changed binding, for example:

```
model_binding: openai/gpt-4o/2024-08-06 -> openai/gpt-4o/2024-11-20
```

Every attempt — admitted or refused — is recorded (`GET /promotions`) and audited.

## 3. Invalidate it by changing behavior

Edit `examples/workloads/repo-agent.yaml` to change a behavior-affecting field — the model
identity in `spec.runtime`, or add/remove a tool — and re-register:

```bash
hiveplane register examples/workloads/repo-agent.yaml
hiveplane certs list --workload repo-agent      # status is now uncertified
```

The old attestation is still verifiable (history is immutable), but it no longer admits the
new artifact. Try to run and you will be refused. This is the promotion gate working.

## 4. Re-certify and see the regression diff

```bash
hiveplane certify repo-agent --context staging
hiveplane certs compare-baseline repo-agent <after-cert-id>
```

Each `pass → fail` is a regression with latency, token, and cost deltas; a regression on a
`critical` task is a **critical regression** and feeds the promotion refusal. Every changed
task carries replayable frames (task contract + trace id) so you can replay it
deterministically. An unchanged workload produces an empty diff.

Promote again once green, or re-certify and promote in one step:

```bash
hiveplane promote repo-agent --recertify
```

## 5. Simulate drift

Drift is measured against the agent's **own baseline**, not a global standard. A single
exceeding run is only a warning; quarantine needs either
`drift.required_consecutive_failures` consecutive exceeding runs or a strong signal (a
critical failure, or a drop at least 2× the threshold).

Assess a fresh evaluation:

```bash
hiveplane drift assess repo-agent --pass-rate 0.80 --tasks-failed 4
hiveplane drift due
hiveplane drift expiries
```

If the assessment crosses the threshold enough times, the workload is auto-quarantined:
production admission is revoked immediately, an in-flight-cancellation policy may apply, the
owner is notified, and the action is audited. Quarantine manually when a human sees trouble
first:

```bash
hiveplane drift quarantine repo-agent --reason "manual hold" --operator alice
hiveplane drift quarantines --workload repo-agent
```

## 6. Reinstate — only with a fresh certification

There is no override. Reinstatement requires a fresh passing certification:

```bash
hiveplane certify repo-agent --context production
hiveplane drift reinstate <quarantine-id> --operator alice
```

A failing or unchanged workload stays quarantined. A stable agent is never falsely
quarantined — the false-positive controls exist precisely so operators trust the signal.

## 7. Know when to trust a failing gate

- **Promotion refused** → re-certify; never force admission to an old hash.
- **Drift warning (one run)** → investigate, but no action is forced.
- **Quarantine** → compare-baseline, fix the cause, re-certify, reinstate.
- **Attestation verify fails** → the log or signature is tampered with; treat as a security
  event and do not admit.

```bash
hiveplane verify <attestation-id>          # public, unauthenticated
```

**Next:** [Tutorial 3 — Autonomy: Triggers & Pipelines](03-autonomy-triggers-pipelines.md).

## See Also

- [User Guide — Promotion & Re-certification](../USER_GUIDE.md)
- [Certification v2 design](../design/certification-v2-design.md) · [Learning loop](../design/learning-loop-design.md)
- [Operator Runbook — common incidents](../runbooks/operator-runbook.md)

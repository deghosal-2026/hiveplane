# D27: Learning Loop Design

> Status: draft

**Milestones:** M36 · **Extends:** D10

## Problem

D10's benchmark corpus is static. Production is where agents actually fail, but a real failure produces no lasting artifact — the operator investigates, fixes the prompt or agent, and the same failure recurs. Certification never learns. Meanwhile, scheduled re-certification is the only quality signal, so decay between re-certs is invisible.

The learning loop closes both gaps: operators flag production runs, flagged failures become **reviewed** corpus cases that ship in the next corpus version, and a sampled fraction of production runs is scored by an LLM judge to give continuous quality signal between re-certifications.

## Overview

```
 production run
      │
      ├─ operator feedback (good/bad/failed-with-lesson + notes)
      │      └─▶ candidate case (input + expected) ──▶ MANDATORY REVIEW
      │             ├ approve ─▶ next corpus version ─▶ next certification
      │             └ reject  ─▶ archived with reason
      │
      └─ sampled (%) ─▶ LLM judge vs. versioned rubric ─▶ quality score
                                                          └─▶ health (D16) + drift (D26)
```

Two loops share one principle: production data is evidence, but nothing enters the benchmark or a metric definition without versioned, reviewed human control.

## Design

### Run feedback capture

An operator marks a run through UI, CLI, or API:

| Verdict | Meaning |
|---------|---------|
| `good` | Output correct and useful — reinforces the baseline |
| `bad` | Output wrong or low quality, but no reusable lesson |
| `failed-with-lesson` | A concrete failure with a stated correction — the corpus candidate source |

Every feedback record is attributable (DD-07) and carries free-text notes. `good`/`bad` feed the production quality score; only `failed-with-lesson` produces a corpus candidate.

### Feedback → corpus candidate

A `failed-with-lesson` run is converted into a **candidate** corpus case: the run's input (redacted per retention/PII policy) plus a proposed expected outcome derived from the operator's lesson, the agent's actual output, and any judge hint. The candidate is **inert** — it lives in the candidate store and never affects certification until approved.

### Mandatory review gate

Human review of every candidate is non-negotiable, because a wrong expected outcome **poisons certification**: an incorrect expectation makes a correct agent fail and a broken agent pass. Reviewers see the original run trace, the proposed expected outcome, and the diff against the current corpus. Approval promotes the candidate into the next corpus version; rejection archives it with a reason. There is no auto-approval path for feedback-derived cases.

### Corpus versioning integration

Approved candidates are staged for the **next** corpus version; the current version is immutable. When the version is cut, its task list includes the accepted cases, and the next certification run executes against it. Attestations bind `(corpus_id, corpus_version)`, so a corpus bump invalidates prior certifications for promotion purposes and forces a re-cert (D10/D20). Old attestations are superseded, never reused.

### Online eval sampling

A configurable percentage of production runs is scored by an LLM judge against a **versioned rubric**. Selection is deterministic — `hash(run_id) mod 100 < sample_rate` — so the same run always samples (or does not), independent of timing or retries. The judge returns a bounded score and a per-criterion breakdown.

### Judge results → health / drift signals

Judge scores roll up into a **production quality score** per workload: a rolling-window aggregate surfaced as a first-class health signal (D16) alongside readiness, failure rate, SLO burn, and drift. A sustained quality dip can trigger early re-certification (D26) before the scheduled cadence catches it.

### Sampling guardrails

- **Cost cap:** sampling respects a per-workload/per-period judge budget; sampling stops when the cap is hit.
- **PII-safe:** runs marked PII (or whose inputs match sensitive patterns) are never sampled, and judge inputs are redacted.
- **Deterministic selection:** stable per-run hash, no random drift in which runs are scored.
- **Rubric versioning:** every score records the rubric version used. Changing a rubric changes score semantics; without versioning, a rubric edit would look like a quality change. Rubric versions are immutable once used.

## Data Model

```
run_feedback        id, run_id, workload_id, verdict, notes, operator, created_at
corpus_candidates   id, source_run_id, workload_id, input JSONB, expected JSONB,
                    status, proposed_by, created_at
candidate_reviews   id, candidate_id, reviewer, decision, reason, reviewed_at
corpus_versions     id, corpus_id, version, task_ids, created_at
eval_samples        id, run_id, workload_id, rubric_version, sampled_at
judge_scores        id, eval_sample_id, rubric_version, score, criteria JSONB, created_at
rubrics             id, name, version, criteria JSONB, immutable
```

## Interfaces / API

```
POST  /runs/{id}/feedback        { verdict, notes }
GET   /corpus/candidates?workload=&status=
POST  /corpus/candidates/{id}/approve   { reviewer }
POST  /corpus/candidates/{id}/reject    { reviewer, reason }
GET   /eval/samples?workload=&since=
GET   /workloads/{id}/quality?window=
```

```
hiveplane feedback <run_id> --verdict failed-with-lesson --notes "..."
hiveplane corpus candidates [--workload <id>]
hiveplane corpus approve <candidate_id>
hiveplane corpus reject <candidate_id> --reason "..."
hiveplane eval samples [--workload <id>]
```

## Failure Modes

| Condition | Behavior |
|-----------|----------|
| Operator flags a run incorrectly | Candidate requires human review; a wrong expectation cannot enter the corpus unreviewed |
| Poisoned expected outcome | Rejected at review; archived with reason; never active |
| Judge budget exhausted | Sampling pauses; quality score reports reduced coverage |
| PII-marked run selected | Skipped before judge invocation |
| Rubric edited or judge model changed | New immutable rubric version pins the evaluator identity; old scores keep their version; no silent metric drift |
| Quality dip | Surfaces in health and may trigger early re-cert; does not directly quarantine (D26 owns that) |
| Corpus version cut | Prior certifications superseded for promotion; re-cert required |

## Security

- Candidate inputs are redacted per tenant retention/PII policy before storage; secrets never enter candidates or judge prompts.
- Feedback, review, and rejection actions are attributable and audited (DD-07).
- The judge runs under the same budget and policy boundary as any workload; it is not a privileged path.
- Rubrics and corpus versions are immutable once referenced by a score or attestation.
- No auto-approval: the review gate is the only path from production failure to benchmark case.

## Testing

- A flagged `failed-with-lesson` run becomes a candidate; approval lands it in the next corpus version and the next certification includes it.
- A rejected candidate never enters the corpus and is archived with a reason.
- Sampling selection is deterministic and respects the configured rate.
- Sampling never selects PII-marked runs and stops at the cost cap.
- Judge scores surface as a production quality signal; a seeded quality dip alerts before the scheduled re-cert.
- A rubric version change does not retroactively alter existing scores.

## Open Questions

- Who reviews candidates — the workload owner, a central QA function, or both?
- Should `bad` feedback with a strong judge signal be convertible to a candidate without an explicit `failed-with-lesson` verdict?
- How is a corpus candidate's expected outcome validated when the task is a rubric check rather than deterministic?
- What sample rate balances judge cost against early drift detection per workload class?

## See Also

- [Certification Pipeline Design](certification-pipeline-design.md) (D10) — corpus, certification, drift
- [Corpus-Fixture Coupling Spec](corpus-fixture-coupling.md) (D20) — task contracts, versioning, negative proof
- [Certification v2 Design](certification-v2-design.md) (D26) — drift, expiry, promotion binding
- [Agent Health Design](agent-health-design.md) (D16) — production quality as a health signal
- [LLM Provider Design](llm-provider-design.md) (D17) — pinned judge model identity
- [PRD 05: Features](../prd/05-features.md) — certification learns from production
- [PRD 09: Roadmap](../prd/09-roadmap.md) — pillar C
- [WBS v0.2.0 Part 6](../wbs/v0.2.0/wbs-v0.2.0-part6-provenance-learning.md) (M36)

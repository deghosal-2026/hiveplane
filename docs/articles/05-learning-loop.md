# The Learning Loop: Agents That Get Better From Production

**Status:** draft · **Pillar:** feedback → corpus, online eval

## Thesis

Production is where agents actually fail. HivePlane turns operator feedback into reviewed
benchmark cases and continuously samples live runs for quality — so the certification corpus
improves with every incident instead of staying frozen at launch.

## Audience

Teams whose agent quality quietly decays after launch, and eval-minded engineers who want the
benchmark to grow from real failures.

## Outline

1. **Flag a run.** Mark a terminal run `good`, `bad`, or `failed-with-lesson` (notes required).
2. **Automatic corpus candidate.** A `failed-with-lesson` run proposes an inert candidate
   (input + proposed expected outcome). Nothing enters the benchmark unreviewed.
3. **The review gate.** Approve/reject is the only path to a benchmark task; rejected
   candidates are archived with a reason.
4. **Versioned corpora.** Approved candidates stage into the *next* corpus version; the next
   certification runs it, and the attestation binds the new `corpus_version` (a bump forces a
   re-cert).
5. **Online eval.** A deterministic sample of production runs is scored by a versioned-rubric
   judge; PII and over-cap runs are skipped; scores roll into a quality signal with a dip flag
   that can fire burn-through/throttle.

## Evidence to link

- [Learning loop design](../design/learning-loop-design.md)
- Field test S22 (cache invalidation on re-cert)
- CLI: `hiveplane feedback`, `hiveplane corpus candidates|approve|reject`, `hiveplane eval`

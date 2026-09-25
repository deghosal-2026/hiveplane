# S1-certify-three (STALE — pre-rewire artifact)

This directory is a **leftover from the pre-rewire harness** (2026-09-25, runs
001923Z–004643Z), when S1 still certified the old trio
(`repo-agent`, `docs-agent`, `incident-agent`) through `examples/*` assets.

It contains only `workloads_used.json`, which at the time recorded the *examples*-based
wiring — that artifact is what proved the harness was pointed at the wrong assets and
triggered the rewire to `field_test/*`.

**Do not read this as current evidence.** The live S1 evidence lives in
`../S1-certify-tier1/`. Kept for provenance of the rewire decision; see `../NOTES.md`
("What changed in the harness").
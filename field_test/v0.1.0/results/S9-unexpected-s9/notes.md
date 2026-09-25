# S9-unexpected-s9 (harness artifact — preserved)

This directory is not a scenario. It records a **harness crash** during the first direct
S9 invocation (2026-09-25): the sweep guard caught an unexpected exception and wrote
`unexpected_error.json`.

## What happened

```
ValueError: '/Users/deghosal/desktop/.../results/S9-fan-out' is not in the subpath of
'/Users/deghosal/Desktop/.../hiveplane'
```

macOS paths are case-insensitive but Python's `Path.relative_to` is a string comparison:
the shell reported the repo root with a lower-case `desktop`, while the script's resolved
`ROOT` carried `Desktop`. The **scenario logic had already passed** — it reached the
`record("S9", "fan-out", "pass", ...)` call — but evidence recording blew up.

## Fix

`scripts/field_test_runner.py` now resolves the results dir (`Path(args.results_dir).resolve()`)
so both sides of the comparison share one canonical case. The immediate rerun of S9 passed
and wrote its evidence to `../S9-fan-out/`.

Kept as a lesson: on macOS, always canonicalize paths before `relative_to`/`parents`
comparisons; a "pass" is not recorded until its evidence is written.
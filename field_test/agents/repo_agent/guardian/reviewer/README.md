# reviewer — Policy Review Engine

Applies governance policy rules to detected AI code.

- `engine.py` — Orchestrates rule evaluation, computes risk level
- `rules.py` — Built-in rules: hallucinated-api, missing-error-handling, hardcoded-secrets
- `models.py` — Violation and ReviewResult dataclasses

Input: `list[DetectionResult]` + `PolicyConfig`
Output: `ReviewResult` with violations and risk level

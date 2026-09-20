# guardian — AI Code Guardian Python package

Root package for the Guardian CI bot. Submodules:

- `detector/` — Scans PR diffs for AI-generated code markers
- `policy/` — Loads and validates `guardian-policy.yml`
- `reviewer/` — Applies policy rules to detected code
- `pr/` — GitHub API client, comments, labels, check runs
- `audit/` — Append-only JSONL audit trail
- `metrics/` — Aggregation and Prometheus export
- `server/` — FastAPI dashboard server

Entry points: `cli.py` (CLI), `action.py` (GitHub Actions)

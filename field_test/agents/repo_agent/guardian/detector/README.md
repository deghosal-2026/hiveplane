# detector — AI Code Detection Engine

Identifies AI-generated code in PR diffs.

- `marker.py` — Phase 1: regex-based marker detection (Copilot, Cursor, Claude Code attribution strings)
- `style.py` — Phase 2: LLM-based style classification (stub in Phase 1)
- `models.py` — DetectionResult and MarkerMatch dataclasses

Output: `list[DetectionResult]` — one per file with AI-attributed code, including tool confidence.

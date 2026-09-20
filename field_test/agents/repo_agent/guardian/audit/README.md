# audit — Immutable Audit Trail

Append-only JSONL event log for all Guardian decisions.

- `models.py` — AuditEntry dataclass with version, timestamp, event ID, action, results
- `logger.py` — File-per-repo logger with append semantics (no modification API)

Storage: `~/.guardian/audit/{repo}/audit.jsonl` by default

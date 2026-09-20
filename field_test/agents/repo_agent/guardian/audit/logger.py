"""Append-only JSONL audit logger."""

import json
from datetime import datetime
from pathlib import Path

from guardian.audit.models import AuditEntry


class AuditLogger:
    def __init__(self, repo: str, audit_dir: str = "~/.guardian/audit"):
        self.repo = repo
        safe_repo = repo.replace("/", "_")
        self.path = Path(audit_dir).expanduser() / safe_repo / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: AuditEntry) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")

    def read(self, since: datetime | None = None) -> list[AuditEntry]:
        if not self.path.exists():
            return []
        entries: list[AuditEntry] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = AuditEntry(**data)
                if since is not None:
                    entry_ts = datetime.fromisoformat(entry.timestamp)
                    if entry_ts < since:
                        continue
                entries.append(entry)
        return entries

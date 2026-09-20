from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from oncall_rag.alert_schema import Alert, AlertResult
from oncall_rag.config import Config

logger = logging.getLogger("oncall-rag.alert-processor")

INBOX_DIR = Path("alerts/inbox")
OUTBOX_DIR = Path("alerts/outbox")
ARCHIVE_DIR = Path("alerts/archive")


def _build_question(alert: Alert) -> str:
    parts = [alert.title]
    if alert.message:
        parts.append(alert.message)
    return " ".join(parts)


def process_alerts(config: Config) -> dict:
    """
    Scan INBOX_DIR for .json alert files, process each through the RAG
    pipeline, write results to OUTBOX_DIR, and move originals to ARCHIVE_DIR.
    """
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    inbox_files = sorted(INBOX_DIR.glob("*.json"))
    if not inbox_files:
        return {"processed": 0, "errors": 0}

    from oncall_rag.responder import query_runbooks

    processed = 0
    errors = 0

    for path in inbox_files:
        try:
            raw = json.loads(path.read_text())
            alert = Alert(**raw)
            question = _build_question(alert)

            raw_answer = query_runbooks(question, config)

            sources: list[str] = []
            if "\n\nSources:\n" in raw_answer:
                answer_text, sources_text = raw_answer.split("\n\nSources:\n", 1)
                sources = [
                    s.strip("- ").strip()
                    for s in sources_text.strip().split("\n")
                    if s.strip()
                ]
            elif raw_answer.startswith("Sources:\n"):
                answer_text = ""
                sources_text = raw_answer[len("Sources:\n"):]
                sources = [
                    s.strip("- ").strip()
                    for s in sources_text.strip().split("\n")
                    if s.strip()
                ]
            else:
                answer_text = raw_answer

            result = AlertResult(
                alert_id=alert.alert_id,
                title=alert.title,
                processed_at=datetime.now(timezone.utc).isoformat(),
                answer=answer_text or "I don't have a runbook for this.",
                sources=sources,
                has_runbook=bool(sources),
            )

            out_path = OUTBOX_DIR / f"{alert.alert_id}.json"
            out_path.write_text(
                json.dumps(
                    {
                        "alert_id": result.alert_id,
                        "title": result.title,
                        "processed_at": result.processed_at,
                        "answer": result.answer,
                        "sources": result.sources,
                        "has_runbook": result.has_runbook,
                    },
                    indent=2,
                )
            )

            archive_path = ARCHIVE_DIR / path.name
            shutil.move(str(path), str(archive_path))

            processed += 1
            logger.info("Processed alert %s: has_runbook=%s", alert.alert_id, result.has_runbook)

        except Exception:
            logger.exception("Failed to process alert %s", path.name)
            errors += 1

    return {"processed": processed, "errors": errors}

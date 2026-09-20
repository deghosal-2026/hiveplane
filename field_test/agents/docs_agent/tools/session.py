from __future__ import annotations
import json
import os
import sqlite3
from typing import Any


_SESSION_DB = "logs/sessions.db"


def _conn() -> sqlite3.Connection:
    os.makedirs("logs", exist_ok=True)
    conn = sqlite3.connect(_SESSION_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "  thread_id TEXT PRIMARY KEY,"
        "  config TEXT NOT NULL,"
        "  completed INTEGER DEFAULT 0"
        ")"
    )
    return conn


def save_session(thread_id: str, config: dict[str, Any]) -> None:
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (thread_id, config) VALUES (?, ?)",
        (thread_id, json.dumps(config)),
    )
    conn.commit()
    conn.close()


def mark_completed(thread_id: str) -> None:
    conn = _conn()
    conn.execute("UPDATE sessions SET completed = 1 WHERE thread_id = ?", (thread_id,))
    conn.commit()
    conn.close()


def list_incomplete_sessions() -> list[dict[str, Any]]:
    conn = _conn()
    rows = conn.execute(
        "SELECT thread_id, config FROM sessions WHERE completed = 0 ORDER BY rowid DESC"
    ).fetchall()
    conn.close()
    result = []
    for tid, cfg_json in rows:
        cfg = json.loads(cfg_json)
        result.append({"thread_id": tid, "config": cfg})
    return result


def get_session(thread_id: str) -> dict[str, Any] | None:
    conn = _conn()
    row = conn.execute(
        "SELECT config FROM sessions WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None
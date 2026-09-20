from __future__ import annotations
import datetime
import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

_LOG_DIR = "logs"


def _ensure_log_dir() -> str:
    os.makedirs(_LOG_DIR, exist_ok=True)
    return _LOG_DIR


def _jsonl_path() -> str:
    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    return os.path.join(_ensure_log_dir(), f"run-{ts}.jsonl")


_jsonl_file: str | None = None


def init_logger(verbose: bool = False) -> str:
    global _jsonl_file
    _jsonl_file = _jsonl_path()

    fmt = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(stream=sys.stderr, format=fmt, level=level, datefmt="%Y-%m-%d %H:%M:%S")
    logger.info("Logging to %s", _jsonl_file)
    return _jsonl_file


class NodeLogger:
    def __init__(self, node_name: str):
        self.node_name = node_name
        self.start: float = 0.0
        self._log = logging.getLogger(f"node.{node_name}")

    def info(self, msg: str, **extra: Any) -> None:
        self._log.info("%s", msg)
        self._write_jsonl("INFO", msg, **extra)

    def warn(self, msg: str, **extra: Any) -> None:
        self._log.warning("%s", msg)
        self._write_jsonl("WARNING", msg, **extra)

    def error(self, msg: str, **extra: Any) -> None:
        self._log.error("%s", msg)
        self._write_jsonl("ERROR", msg, **extra)

    def _write_jsonl(self, level: str, msg: str, **extra: Any) -> None:
        if _jsonl_file is None:
            return
        entry: dict[str, Any] = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "node": self.node_name,
            "level": level,
            "msg": msg,
        }
        if self.start:
            entry["duration_ms"] = int((time.time() - self.start) * 1000)
        entry.update(extra)
        try:
            with open(_jsonl_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass
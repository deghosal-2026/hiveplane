"""Progress hooks for the docker suite.

When the runner sets status/heartbeat files, this plugin keeps them fresh and
records the currently executing test so long L4 waits are observable.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path


def _write(path: str, value: str) -> None:
    if not path:
        return
    Path(path).write_text(value, encoding="utf-8")


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    status_path = os.environ.get("HIVEPLANE_DOCKER_RUN_STATUS_FILE", "")
    heartbeat_path = os.environ.get("HIVEPLANE_DOCKER_RUN_HEARTBEAT_FILE", "")
    _write(status_path, f"running {nodeid}")
    _write(heartbeat_path, datetime.now(UTC).isoformat())


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    heartbeat_path = os.environ.get("HIVEPLANE_DOCKER_RUN_HEARTBEAT_FILE", "")
    _write(heartbeat_path, datetime.now(UTC).isoformat())

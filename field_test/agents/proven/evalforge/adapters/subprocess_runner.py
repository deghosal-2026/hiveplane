"""Common subprocess runner for sandboxed agent execution.

Provides :func:`run_agent_in_subprocess` which handles the full lifecycle of
launching an agent subprocess: constructing ``SandboxConfig``, passing JSON
input on stdin, enforcing timeouts, and normalizing errors. Used by the
subprocess adapter directly and by the python_import adapter when sandboxed.

Supports optional Docker container execution via ``container_runtime == "docker"``
in the config and fixture environment injection.

Exports:
    run_agent_in_subprocess: Launch an agent command with JSON input and
        return its stdout.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from evalforge.models.errors import AdapterError, AgentTimeoutError
from evalforge.security.sandbox import DockerConfig, SandboxConfig, run_in_container, sandboxed_run


def _find_evalforge_path() -> str:
    """Return the parent directory containing the ``evalforge`` package.

    This is added to ``PYTHONPATH`` so the agent subprocess can import
    ``evalforge.fixtures`` (e.g. ``from evalforge.fixtures import ToolStub``)
    when running in fixture mode.
    """
    return str(Path(__file__).resolve().parent.parent.parent)


def run_agent_in_subprocess(
    payload: dict[str, Any],
    agent_cmd: list[str],
    config: dict[str, Any],
) -> str:
    """Run an agent command with JSON payload on stdin, return stdout.

    Handles three execution modes:
    1. Docker container execution (``container_runtime == "docker"``)
    2. Sandboxed local execution (``sandbox == True``)
    3. Plain local execution (no sandbox)

    In fixture mode, sets ``EVALFORGE_FIXTURES`` and ``EVALFORGE_FIXTURES_DIR``
    environment variables for the agent process.

    Args:
        payload: The invocation payload dict, serialized as JSON on stdin.
        agent_cmd: The command and arguments as a list of strings (no shell).
        config: Configuration dict; may include ``timeout_seconds``, ``sandbox``,
            ``env``, ``cwd``, ``fixtures``, ``fixtures_dir``, and
            ``container_runtime``.

    Returns:
        The agent's stdout as a string.

    Raises:
        AdapterError: If the agent exits with a non-zero code, fails to
            launch, or stderr contains error information.
        AgentTimeoutError: If the agent exceeds the configured timeout.
    """
    timeout = float(config.get("timeout_seconds", 120))
    sandbox = SandboxConfig(enabled=bool(config.get("sandbox", False)))
    extra_env: dict[str, str] = dict(config.get("env", {}) or {})
    cwd = config.get("cwd")

    if config.get("container_runtime") == "docker":
        dc = DockerConfig()
        result = run_in_container(agent_cmd, json.dumps(payload), dc, timeout)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise AdapterError(
                f"container exited with code {result.returncode}: {stderr or '(no stderr)'}"
            )
        return result.stdout or ""

    if config.get("fixtures"):
        extra_env["EVALFORGE_FIXTURES"] = "1"
        extra_env["EVALFORGE_FIXTURES_DIR"] = config.get("fixtures_dir", "scenarios/fixtures")
        evalforge_path = _find_evalforge_path()
        existing = extra_env.get("PYTHONPATH", "")
        extra_env["PYTHONPATH"] = (
            f"{evalforge_path}{':' + existing if existing else ''}"
        )
    try:
        result = sandboxed_run(
            args=agent_cmd,
            config=sandbox,
            timeout=timeout,
            input=json.dumps(payload),
            env=extra_env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentTimeoutError(f"agent exceeded {timeout}s timeout") from exc
    except OSError as exc:
        raise AdapterError(f"failed to launch agent: {exc}") from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise AdapterError(
            f"agent exited with code {result.returncode}: {stderr or '(no stderr)'}"
        )
    return result.stdout or ""

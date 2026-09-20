"""IsolatedAdapter: run agents in isolated subprocesses for any framework type.

Spawns a separate Python process per scenario to:
- Avoid import side effects from agent dependencies
- Isolate framework state (LangGraph graphs, PydanticAI agents)
- Support sandbox mode (env stripping)
- Capture stdout/stderr separately

The worker process is launched via ``python -m evalforge.adapters.worker``
with a JSON payload on stdin. Supported adapter types include all framework
adapters registered in ``ADAPTERS`` (``langgraph``, ``pydantic-ai``,
``crewai``, ``openai-agents``, ``smolagents``, ``autogen``, ``llamaindex``,
``claude``, ``adk``).

Exports:
    IsolatedAdapter: Adapter that runs agents in isolated subprocesses.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from evalforge.adapters.base import Adapter, _inject_fixtures
from evalforge.models.errors import AdapterError, AgentTimeoutError
from evalforge.security.sandbox import SandboxConfig, sandboxed_run


class IsolatedAdapter(Adapter):
    """Runs agents in isolated subprocesses for any framework type.

    Spawns a separate Python process per scenario, passing a worker payload
    via stdin. The worker imports the agent module, invokes the agent, and
    writes a JSON run envelope to stdout.

    Attributes:
        name: Identifier "isolated".
    """

    name = "isolated"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Invoke the agent in an isolated subprocess.

        Args:
            payload: The invocation payload dict.
            config: Adapter configuration; requires ``adapter_type`` (one of
                "langgraph" or "pydantic-ai"), ``module`` (Python module path),
                and optionally ``function``, ``model``, ``timeout_seconds``,
                ``sandbox``, and ``env``.

        Returns:
            A parsed run envelope dict from the worker process.

        Raises:
            AdapterError: If required config is missing, the worker fails to
                launch, exits with a non-zero code, or produces invalid JSON.
            AgentTimeoutError: If the worker exceeds the configured timeout.
        """
        adapter_type = config.get("adapter_type")
        if not adapter_type:
            raise AdapterError("isolated adapter requires `adapter_type` in config")
        from evalforge.adapters.factory import ADAPTERS
        if adapter_type not in ADAPTERS:
            raise AdapterError(f"unsupported adapter_type: {adapter_type}")

        module = config.get("module")
        if not module:
            raise AdapterError("isolated adapter requires `module` in config")

        function = config.get("function", "build_agent")
        model = config.get("model")
        timeout = float(config.get("timeout_seconds", 120))

        _inject_fixtures(payload, config)

        worker_payload = {
            "adapter_type": adapter_type,
            "module": module,
            "function": function,
            "model": model,
            "payload": payload,
        }

        args = [sys.executable, "-m", "evalforge.adapters.worker"]
        return _run_worker(args, worker_payload, timeout, config)


def _run_worker(
    args: list[str],
    worker_payload: dict[str, Any],
    timeout: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Launch the worker subprocess and parse its output.

    Args:
        args: The command list to execute (``python -m evalforge.adapters.worker``).
        worker_payload: Dict with adapter metadata and invocation payload.
        timeout: Maximum wall-clock time in seconds.
        config: Adapter configuration (may include sandbox, env, cwd).

    Returns:
        The parsed run envelope dict from the worker's stdout.

    Raises:
        AgentTimeoutError: If the worker exceeds the timeout.
        AdapterError: If the worker fails to launch, exits with non-zero code,
            produces no stdout, or writes invalid JSON.
    """
    sandbox = SandboxConfig(enabled=bool(config.get("sandbox", False)))
    extra_env: dict[str, str] = dict(config.get("env", {}) or {})
    cwd = config.get("cwd")

    try:
        result = sandboxed_run(
            args=args,
            config=sandbox,
            timeout=timeout,
            input=json.dumps(worker_payload),
            env=extra_env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise AgentTimeoutError(f"isolated agent exceeded {timeout}s timeout") from exc
    except OSError as exc:
        raise AdapterError(f"failed to launch isolated worker: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise AdapterError(
            f"isolated worker exited with code {result.returncode}: {stderr or '(no stderr)'}"
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        raise AdapterError("isolated worker produced no stdout")

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"isolated worker stdout is not valid JSON: {exc}") from exc

    if not isinstance(envelope, dict):
        raise AdapterError("isolated worker stdout is not a JSON object")

    return envelope

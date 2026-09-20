"""Python-import adapter: call a Python function as the agent.

The callable runs in a separate process (via multiprocessing) so a hard
timeout and state isolation are guaranteed, matching the spec's worker
isolation model. The agent's contract is ``run(payload) -> dict | str`` where
``payload`` is the invocation payload dict; returning a dict is treated as a
run envelope, returning a string is treated as a plain-text final answer.

The result is shipped back over a :class:`multiprocessing.Queue` so arbitrary
agent exceptions can be captured and normalized instead of crashing the
runner.

When ``sandbox`` is enabled in config, the agent is routed through a
sandboxed subprocess instead of multiprocessing for stronger isolation.

Note: In parallel execution (workers > 1), this adapter uses
multiprocessing.Process which uses "spawn" on macOS. The combined
use of ThreadPoolExecutor + multiprocessing.Process can cause
import path issues. Prefer the subprocess adapter for parallel runs
on macOS, or set workers=1 for python_import.

Exports:
    PythonImportAdapter: Adapter that invokes a Python function as the agent.
"""

from __future__ import annotations

import importlib.metadata
import multiprocessing
from typing import Any

from evalforge.adapters.base import Adapter, _inject_fixtures, parse_agent_stdout
from evalforge.models.adapter_manifest import AdapterManifest
from evalforge.models.errors import AdapterError, AgentTimeoutError


def _agent_worker(module: str, function: str, payload: dict[str, Any], queue: Any) -> None:
    """Run in a child process: import module, call function, send result.

    This function is the target of a ``multiprocessing.Process``. It imports
    the specified module, calls the specified function with the payload, and
    puts a ``("ok", result)`` or ``("error", message)`` tuple on the queue.

    When ``_fixture_mode`` is True in the payload, a :class:`ToolStub` is
    constructed and attached to ``payload["_tool_stub"]`` so the agent can
    use deterministic fixtures instead of live tool calls.

    Args:
        module: The Python module to import.
        function: The function name to call within the module.
        payload: The invocation payload dict passed to the function.
        queue: A ``multiprocessing.Queue`` for shipping the result back.
    """
    import importlib

    try:
        if payload.get("_fixture_mode"):
            from evalforge.fixtures import ToolStub

            fixture_dir = payload.get("_fixtures_dir", "scenarios/fixtures")
            tool_stub = ToolStub(fixture_dir)
            payload["_tool_stub"] = tool_stub
        mod = importlib.import_module(module)
        fn = getattr(mod, function)
        result = fn(payload)
        queue.put(("ok", result))
    except BaseException as exc:
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


class PythonImportAdapter(Adapter):
    """Invoke a Python function as the agent.

    Runs the user's function in a separate ``multiprocessing.Process`` for
    timeout and state isolation. Supports sandbox mode which routes through
    a subprocess instead.

    Attributes:
        name: Identifier "python".
    """

    name = "python"

    def get_manifest(self) -> dict[str, Any]:
        try:
            version = importlib.metadata.version("agent-eval-forge")
        except (importlib.metadata.PackageNotFoundError, OSError):
            version = "0.0.0"
        manifest = AdapterManifest(
            name="python_import",
            version=version,
            capabilities=["env_isolation", "timeout"],
            input_schema={
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "module": {"type": "string"},
                    "function": {"type": "string", "default": "run"},
                    "timeout_seconds": {"type": "number", "default": 120},
                    "sandbox": {"type": "boolean", "default": False},
                    "strict_output": {"type": "boolean", "default": False},
                },
                "required": ["module"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "schema_version": {"type": "string"},
                    "status": {"type": "string"},
                    "output": {"type": "object"},
                    "trajectory": {"type": "object"},
                    "cost": {"type": "object"},
                    "error": {"type": "string"},
                },
            },
        )
        return manifest.to_dict()

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str | dict[str, Any]:
        """Import and invoke a Python function as the agent.

        When sandbox is enabled, delegates to ``subprocess_runner`` with an
        inline script. Otherwise, spawns a ``multiprocessing.Process`` running
        ``_agent_worker`` and waits for the result on a queue.

        Args:
            payload: The invocation payload dict.
            config: Adapter configuration; requires ``module``, optionally
                ``function`` (default "run"), ``sandbox``, and
                ``timeout_seconds``.

        Returns:
            The agent's return value: either a string (raw stdout) or a dict
            (run envelope).

        Raises:
            AdapterError: If module config is missing, the process fails,
                the agent returns an error status, or the return type is
                unexpected.
            AgentTimeoutError: If the agent exceeds the configured timeout.
        """
        module = config.get("module")
        if not module:
            raise AdapterError("python adapter requires `module` in config")
        function = config.get("function", "run")

        _inject_fixtures(payload, config)

        if config.get("sandbox"):
            from evalforge.adapters.subprocess_runner import run_agent_in_subprocess

            agent_cmd = ["python", "-c", f"""
import importlib, sys, json
mod = importlib.import_module('{module}')
fn = getattr(mod, '{function}')
payload = json.loads(sys.stdin.read())
result = fn(payload)
if isinstance(result, dict):
    print(json.dumps(result))
else:
    print(result)
"""]
            config_with_sandbox = {**config, "sandbox": True}
            stdout = run_agent_in_subprocess(payload, agent_cmd, config_with_sandbox)
            return parse_agent_stdout(stdout, strict=bool(config.get("strict_output", False)))

        timeout = float(config.get("timeout_seconds", 120))
        try:
            queue: multiprocessing.Queue[tuple[str, object]] = multiprocessing.Queue()
            proc = multiprocessing.Process(
                target=_agent_worker,
                args=(module, function, payload, queue),
                daemon=True,
            )
            proc.start()
            proc.join(timeout)
            if proc.is_alive():
                proc.terminate()
                proc.join()
                raise AgentTimeoutError(f"agent exceeded {timeout}s timeout")
            if proc.exitcode != 0:
                raise AdapterError(f"agent process exited with code {proc.exitcode}")
        except multiprocessing.ProcessError as exc:
            raise AdapterError(f"agent process failed: {exc}") from exc

        try:
            status, value = queue.get(timeout=timeout)
        except Exception as exc:
            raise AdapterError(f"agent produced no result: {exc}") from exc
        if status == "error":
            raise AdapterError(str(value))
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value
        raise AdapterError(f"agent function returned unexpected type: {type(value).__name__}")

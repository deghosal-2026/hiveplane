"""The sandboxed child worker (M23, #110, D11).

Runs a workload entrypoint inside the sandboxed subprocess. The worker
re-imports the entrypoint and drives it with an :class:`HttpWorkerContext` so
every tool call and model call routes back to the control-plane boundary over
the token-guarded channel. The final result (or a failure) is reported back the
same way. Launched as ``python -m hiveplane.execution.subprocess_worker``.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from hiveplane.adapters.worker import WorkerContext

from hiveplane.adapters.errors import (
    RunCancelledError,
    ToolCallBlockedError,
    ToolCallDeniedError,
    ToolCallEscalatedError,
    WorkerError,
)
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.core.decision import ActionClass, DataSensitivity
from hiveplane.core.sandbox import ResourceCaps
from hiveplane.execution.sandbox_spec import SandboxWorkerSpec
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult
from hiveplane.llm.models import CompletionResult, Message

_RUN_TOKEN_HEADER = "X-HivePlane-Run-Token"

__all__ = [
    "HttpWorkerContext",
    "SandboxWorkerSpec",
    "Transport",
    "main",
    "run_worker",
]

#: A transport that performs an HTTP call and returns (status, json body).
Transport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str] | None],
    tuple[int, dict[str, Any]],
]


def _default_transport(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    all_headers = {"Content-Type": "application/json"}
    if headers is not None:
        all_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


class HttpWorkerContext:
    """A WorkerContext client that reaches the boundary over the sandbox channel."""

    def __init__(
        self,
        *,
        base_url: str,
        run_id: str,
        token: str,
        transport: Transport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._run_id = run_id
        self._token = token
        self._transport = transport or _default_transport

    @property
    def run_id(self) -> str:
        return self._run_id

    def _url(self, path: str) -> str:
        return f"{self._base_url}/internal/sandbox/{self._run_id}/{path}"

    def _post(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        status, data = self._transport(
            "POST", self._url(path), payload, {_RUN_TOKEN_HEADER: self._token}
        )
        if status >= 400:
            raise WorkerError(f"sandbox channel {path!r} failed: {status} {data}")
        return data

    def _get(self, path: str) -> dict[str, Any]:
        status, data = self._transport(
            "GET", self._url(path), None, {_RUN_TOKEN_HEADER: self._token}
        )
        if status >= 400:
            raise WorkerError(f"sandbox channel {path!r} failed: {status} {data}")
        return data

    def tool_call(
        self,
        tool_id: str,
        *,
        action_class: ActionClass | None = None,
        data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL,
        host: str | None = None,
        output: str | None = None,
    ) -> ToolCallResult:
        """Route a tool call through the boundary; raise on non-allowed outcomes."""
        payload = {
            "tool_id": tool_id,
            "action_class": action_class.value if action_class is not None else None,
            "data_sensitivity": data_sensitivity.value,
            "host": host,
            "output": output,
        }
        result = ToolCallResult(**self._post("tool-call", payload))
        if result.outcome is ToolCallOutcome.DENIED:
            raise ToolCallDeniedError(result)
        if result.outcome is ToolCallOutcome.BLOCKED_INJECTION:
            raise ToolCallBlockedError(result)
        if result.outcome is ToolCallOutcome.ESCALATED:
            raise ToolCallEscalatedError(result)
        return result

    def complete(
        self,
        prompt: str | list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Invoke the bound model through the provider seam on the boundary."""
        messages = (
            [Message(role="user", content=prompt)] if isinstance(prompt, str) else list(prompt)
        )
        payload = {
            "messages": [message.model_dump() for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        return CompletionResult(**self._post("model", payload))

    def report_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Report usage for server-side pricing and budget enforcement."""
        self._post(
            "usage",
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "tool_calls": tool_calls,
                "cost_usd": cost_usd,
            },
        )

    def checkpoint(self) -> None:
        """Yield control: raise when the run has been cancelled.

        Cross-process cooperative pause is out of scope for v0.1.0 (tracked by
        #111); cancellation is surfaced immediately.
        """
        state = self._get("control")
        if state.get("cancelled"):
            raise RunCancelledError()


def _safe_post(
    spec: SandboxWorkerSpec,
    transport: Transport | None,
    path: str,
    payload: dict[str, Any],
) -> None:
    """Best-effort report to the boundary; a failed report must not mask the run."""
    transport = transport or _default_transport
    url = f"{spec.base_url.rstrip('/')}/internal/sandbox/{spec.run_id}/{path}"
    with suppress(Exception):
        transport("POST", url, payload, {_RUN_TOKEN_HEADER: spec.token})


def _apply_resource_limits(caps: ResourceCaps | None) -> None:
    """Apply RLIMIT_AS / RLIMIT_CPU in this (child) process before the agent runs.

    The cap is best-effort: on platforms where the current address space already
    exceeds the requested ceiling (notably macOS, where the inherited virtual
    address space is enormous and cannot be lowered), the limit is skipped with a
    warning so the run still executes. On Linux/Docker the limit applies and a
    subsequent allocation that exceeds it fails (MemoryError/SIGKILL), failing the
    run. RLIMIT_CPU is a secondary cap; the spawner's wall-clock watchdog is the
    primary one.
    """
    if caps is None:
        return
    try:
        limit = caps.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError) as exc:
        sys.stderr.write(
            f"warning: could not apply RLIMIT_AS={caps.memory_mb}MB: {exc}; "
            "running uncapped (caps are enforced on Linux/Docker)\n"
        )
    try:
        cpu = max(1, caps.wall_clock_s)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except (ValueError, OSError):
        pass


def run_worker(
    spec: SandboxWorkerSpec, *, transport: Transport | None = None
) -> int:
    """Run the entrypoint in this process and report the outcome to the boundary."""
    with suppress(Exception):
        _apply_resource_limits(spec.resource_caps)
    entry = EntrypointLoader(root=spec.root).load(spec.entrypoint)
    ctx = HttpWorkerContext(
        base_url=spec.base_url, run_id=spec.run_id, token=spec.token, transport=transport
    )
    try:
        result = entry(spec.task, cast("WorkerContext", ctx))
    except Exception as exc:
        traceback.print_exc()
        _safe_post(
            spec,
            transport,
            "failure",
            {"reason": f"{type(exc).__name__}: {exc}"},
        )
        return 1
    _safe_post(spec, transport, "result", {"result": result})
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``python -m hiveplane.execution.subprocess_worker --spec <json>``."""
    parser = argparse.ArgumentParser(prog="hiveplane.subprocess_worker")
    parser.add_argument("--spec", required=True, help="JSON SandboxWorkerSpec")
    args = parser.parse_args(argv)
    spec = SandboxWorkerSpec.model_validate(json.loads(args.spec))
    return run_worker(spec)


if __name__ == "__main__":
    raise SystemExit(main())

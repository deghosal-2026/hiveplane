"""End-to-end: a sandboxed run executes in the capped child (M23, #110, D11).

A live control plane serves the sandbox channel; the raw-worker adapter launches
the child with resource caps. A clean agent routes its tool call back over the
channel and completes; a memory hog is capped and the run fails.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from hiveplane.api.app import create_app
from hiveplane.config import get_settings
from hiveplane.core.manifest import load_manifest
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.seeding import derive_tool_registrations, seed_tools
from hiveplane.registry.service import RegistryService

_LINUX_ONLY = pytest.mark.skipif(
    sys.platform != "linux", reason="RLIMIT_AS memory cap is Linux-only"
)

ROOT = Path(__file__).resolve().parents[1]
_MODEL = "openai/gpt-4o/2024-08-06"
_CLEAN = (
    "def run(task, ctx):\n"
    "    result = ctx.tool_call('mcp.github.list_pull_requests', host='api.github.com')\n"
    "    return {'ok': True, 'tool': result.tool_id}\n"
)
_HOG = "def run(task, ctx):\n    bytearray(4 * 10**9)\n"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.2):
                return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(f"server at {url} never became ready")


class _LiveServer:
    def __init__(self, app: object, port: int) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self, port: int) -> None:
        self._thread.start()
        _wait_ready(f"http://127.0.0.1:{port}/healthz")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def _with_entrypoint(manifest: AgentWorkload, name: str, entrypoint: str) -> AgentWorkload:
    return manifest.model_copy(
        update={
            "metadata": manifest.metadata.model_copy(update={"name": name}),
            "spec": manifest.spec.model_copy(
                update={
                    "runtime": manifest.spec.runtime.model_copy(
                        update={"entrypoint": entrypoint}
                    )
                }
            ),
        }
    )


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[object, Path]]:
    port = _free_port()
    (tmp_path / "clean_agent.py").write_text(_CLEAN, encoding="utf-8")
    (tmp_path / "hog_agent.py").write_text(_HOG, encoding="utf-8")
    monkeypatch.setenv("HIVEPLANE_EXECUTION__STORE", "memory")
    monkeypatch.setenv("HIVEPLANE_EXECUTION__ADAPTER", "raw-worker")
    monkeypatch.setenv("HIVEPLANE_EXECUTION__SANDBOX_MODE", "subprocess")
    monkeypatch.setenv("HIVEPLANE_EXECUTION__BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("HIVEPLANE_EXECUTION__ENTRYPOINTS_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "HIVEPLANE_EXECUTION__TOOL_FIXTURES", str(ROOT / "deploy" / "testdata" / "tools")
    )
    monkeypatch.setenv("HIVEPLANE_MODEL__PROVIDER", "fake")
    get_settings.cache_clear()
    app = create_app()
    registry: RegistryService = app.state.registry_service
    base = load_manifest(ROOT / "examples" / "workloads" / "repo-agent.yaml")
    seed_tools(registry, derive_tool_registrations(base))
    registry.create(_with_entrypoint(base, "clean-agent", "clean_agent:run"))
    registry.create(_with_entrypoint(base, "hog-agent", "hog_agent:run"))
    server = _LiveServer(app, port)
    server.start(port)
    try:
        yield app, tmp_path
    finally:
        server.stop()


def _wait_terminal(service: object, run_id: str, timeout: float = 30.0) -> Run:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = service.get(run_id)  # type: ignore[attr-defined]
        if run.state in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            return run
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} never reached a terminal state")


def test_clean_sandboxed_run_completes_over_the_channel(
    live: tuple[object, Path],
) -> None:
    app, _ = live
    service = app.state.run_service  # type: ignore[attr-defined]
    run = service.submit(
        workload="clean-agent",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
    )
    service.start(run.id, actor="cli")

    done = _wait_terminal(service, run.id)

    assert done.state is RunState.COMPLETED, done.failure_reason
    assert done.result == {"ok": True, "tool": "mcp.github.list_pull_requests"}


@_LINUX_ONLY
def test_memory_hog_sandboxed_run_is_capped(live: tuple[object, Path]) -> None:
    app, _ = live
    service = app.state.run_service  # type: ignore[attr-defined]
    run = service.submit(
        workload="hog-agent",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity=_MODEL,
    )
    service.start(run.id, actor="cli")

    done = _wait_terminal(service, run.id)

    assert done.state is RunState.FAILED

"""L0 — the built control-plane image is runnable (M23, #143/#93).

Builds the production image and asserts that, inside it:

- the runtime fixtures the sandboxed/certification path reads are present
  (``deploy/testdata/tools`` and ``deploy/testdata/llm/replay.json``);
- every workload entrypoint imports, so the adapter can load it;
- the ``langgraph`` extra and the CLI resolve.

Run with: ``pytest tests/docker -m docker``
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_IMAGE = "hiveplane-fixture-test:latest"

_REQUIRED_FIXTURES = (
    "deploy/testdata/tools/prometheus.query.json",
    "deploy/testdata/tools/pagerduty.acknowledge.json",
    "deploy/testdata/llm/replay.json",
    "deploy/testdata/governance/over-budget.json",
    "deploy/testdata/governance/destructive-call.json",
    "deploy/testdata/governance/large-output.json",
)

_REQUIRED_IMPORTS = (
    "examples.repo_agent",
    "examples.incident_agent",
    "examples.docs_agent",
    "examples.worker",
    "langgraph",
    "hiveplane.cli",
)


@pytest.fixture(scope="module")
def built_image() -> str:
    """Build the production image once for every image assertion."""
    subprocess.run(
        ["docker", "build", "-t", _IMAGE, str(_REPO_ROOT)],
        check=True,
    )
    return _IMAGE


@pytest.mark.docker
def test_image_ships_tool_and_replay_fixtures(built_image: str) -> None:
    missing = [
        path
        for path in _REQUIRED_FIXTURES
        if not _file_exists_in_image(built_image, path)
    ]

    assert missing == [], f"fixtures missing from the image: {missing}"


@pytest.mark.docker
def test_image_imports_every_entrypoint_and_extra(built_image: str) -> None:
    result = _run_in_image(built_image, _import_script())

    assert result.returncode == 0, (
        f"entrypoint import failed inside the image:\n{result.stdout}\n{result.stderr}"
    )


def _import_script() -> str:
    targets = repr(_REQUIRED_IMPORTS)
    return (
        "import importlib\n"
        f"targets = {targets}\n"
        "failures = []\n"
        "for name in targets:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception as exc:  # noqa: BLE001\n"
        "        failures.append((name, repr(exc)))\n"
        "if failures:\n"
        "    raise SystemExit(f'import failures: {failures}')\n"
        "print('all entrypoints import')\n"
    )


def _file_exists_in_image(image: str, path: str) -> bool:
    code = (
        "from pathlib import Path; "
        f"raise SystemExit(0 if Path({path!r}).is_file() else 1)"
    )
    return _run_in_image(image, code).returncode == 0


def _run_in_image(image: str, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", image, "-c", code],
        capture_output=True,
        text=True,
    )


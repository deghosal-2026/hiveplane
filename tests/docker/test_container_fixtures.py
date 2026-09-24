"""Assert the built control-plane image ships the runtime fixtures (M23, #143).

The field test serves tool output from ``deploy/testdata/tools`` and LLM replay
data from ``deploy/testdata/llm``. Both live outside the package, so the image
must copy ``deploy/testdata`` explicitly or sandboxed/certification runs inside
the container fail to resolve fixtures.

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
)


@pytest.fixture(scope="module")
def built_image() -> str:
    """Build the production image once for every fixture assertion."""
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


def _file_exists_in_image(image: str, path: str) -> bool:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            image,
            "-c",
            f"from pathlib import Path; raise SystemExit(0 if Path({path!r}).is_file() else 1)",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0

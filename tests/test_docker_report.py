"""Tests for the docker run report generator (M23, #93/#96).

The generator turns a JUnit XML run into the detailed Markdown report and
enforces the zero-skip policy by exiting non-zero when any test skipped.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "docker_report.py"

_JUNIT_PASS = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="2" failures="0" errors="0" skipped="0">
    <testcase classname="tests.docker.test_api_contract" name="test_manifest_schema"
      file="tests/docker/test_api_contract.py" time="0.01"/>
    <testcase classname="tests.docker.test_llm_matrix" name="test_local_llm_returns_a_completion"
      file="tests/docker/test_llm_matrix.py" time="1.20"/>
  </testsuite>
</testsuites>
"""

_JUNIT_SKIP = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="1" failures="0" errors="0" skipped="1">
    <testcase classname="tests.docker.test_stack_health" name="test_readyz_reports_ready"
      file="tests/docker/test_stack_health.py" time="0.01">
      <skipped message="no stack">skipped</skipped>
    </testcase>
  </testsuite>
</testsuites>
"""


_JUNIT_FAIL = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0">
    <testcase classname="tests.docker.test_control_loop"
      name="test_register_certify_run_and_deliver"
      file="tests/docker/test_control_loop.py" time="3.40"/>
    <testcase classname="tests.docker.test_stack_health"
      name="test_observability_services_are_healthy"
      file="tests/docker/test_stack_health.py" time="2.10">
      <failure message="tempo not healthy">503</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


def _run(tmp_path: Path, junit: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    junit_path = tmp_path / "junit.xml"
    junit_path.write_text(junit, encoding="utf-8")
    environment = tmp_path / "environment.json"
    environment.write_text(
        json.dumps(
            {"git_sha": "abc123", "llm_model": "Qwen3-4B-Instruct-2507-4bit"}
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.md"
    (tmp_path / "pytest.log").write_text("log", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--junit",
            str(junit_path),
            "--environment",
            str(environment),
            "--log-dir",
            str(tmp_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    return result, output


def test_report_renders_layers_and_overall_pass(tmp_path: Path) -> None:
    result, output = _run(tmp_path, _JUNIT_PASS)

    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert "**Overall: PASS**" in text
    assert "## Issues / Learnings" in text
    assert "L2" in text and "API contract" in text
    assert "L7" in text and "LLM matrix" in text
    assert "Qwen3-4B-Instruct-2507-4bit" in text
    assert "`pytest.log`" in text


def test_report_fails_when_a_test_is_skipped(tmp_path: Path) -> None:
    result, output = _run(tmp_path, _JUNIT_SKIP)

    assert result.returncode != 0
    text = output.read_text(encoding="utf-8")
    assert "**Overall: FAIL**" in text
    assert "Zero-skip policy violated" in text


def test_report_fails_when_a_test_fails(tmp_path: Path) -> None:
    result, output = _run(tmp_path, _JUNIT_FAIL)

    assert result.returncode != 0
    text = output.read_text(encoding="utf-8")
    assert "**Overall: FAIL**" in text
    assert "L1" in text and "failed" in text.lower()

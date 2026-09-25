"""L3 — operator UI screens render against the live stack (M23, #93/#95).

Drives Playwright against the running UI and captures deterministic screenshots
into ``docs/field-test/v0.1.0/screenshots/<scenario-id>/<step>-<name>.png``
(reruns overwrite the same paths, #95).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from hiveplane.core.manifest import load_manifest

_API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
_UI_BASE = os.environ.get("HIVEPLANE_UI_URL", "http://localhost:3001").rstrip("/")
_ROOT = Path(__file__).resolve().parents[2]
_SCREENSHOTS = _ROOT / "docs" / "field-test" / "v0.1.0" / "screenshots"
_ENV_FILE = _ROOT / ".env.local"


def _canonical_identity() -> str:
    """The local model identity the stack certifies and binds runs to."""
    if _ENV_FILE.is_file():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("HIVEPLANE_MODEL__DEFAULT_MODEL="):
                return line.partition("=")[2].strip()
    return "openai/gpt-4o/2024-08-06"


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        f"{_API_BASE}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, body


def _shot(page: Page, scenario: str, step: int, name: str) -> Path:
    directory = _SCREENSHOTS / scenario
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{step:02d}-{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


@pytest.fixture(scope="module")
def seeded_run() -> str:
    """Register repo-agent and create a started run for the detail screen."""
    payload = load_manifest(_ROOT / "examples" / "workloads" / "repo-agent.yaml").model_dump(
        by_alias=True, mode="json"
    )
    _request("POST", "/workloads", payload)
    status, run = _request(
        "POST",
        "/runs",
        {
            "workload": "repo-agent",
            "caller": "field-test",
            "context": "sandbox",
            "model_identity": _canonical_identity(),
        },
    )
    assert status == 201, run
    _request("POST", f"/runs/{run['id']}/start")
    return str(run["id"])


@pytest.mark.docker
def test_fleet_screen(page: Page) -> None:
    page.goto(f"{_UI_BASE}/")

    expect(page.get_by_role("heading", name="Fleet")).to_be_visible()
    _shot(page, "ui-fleet", 1, "fleet")


@pytest.mark.docker
def test_run_detail_screen(page: Page, seeded_run: str) -> None:
    page.goto(f"{_UI_BASE}/runs/{seeded_run}")

    expect(page.get_by_role("heading", name=f"Run {seeded_run}")).to_be_visible()
    _shot(page, "ui-run-detail", 1, "run-detail")


@pytest.mark.docker
def test_approvals_screen(page: Page) -> None:
    page.goto(f"{_UI_BASE}/approvals")

    expect(page.get_by_role("heading", name="Approval queue")).to_be_visible()
    _shot(page, "ui-approvals", 1, "approvals")


@pytest.mark.docker
def test_certifications_screen(page: Page) -> None:
    page.goto(f"{_UI_BASE}/certifications")

    expect(page.get_by_role("heading", name="Certification dashboard")).to_be_visible()
    _shot(page, "ui-certifications", 1, "certifications")


@pytest.mark.docker
def test_spend_screen(page: Page) -> None:
    page.goto(f"{_UI_BASE}/spend")

    expect(page.get_by_role("heading", name="Spend")).to_be_visible()
    _shot(page, "ui-spend", 1, "spend")

"""L3 (v0.2.0) — operator UI v2 screens (Playwright) + screenshot capture (M52, M61-06).

Drives Playwright against the running UI and captures deterministic screenshots into
``docs/field-test/v0.2.0/screenshots/<scenario-id>/<step>-<name>.png`` (reruns overwrite
the same paths). Covers the v0.2.0 screens: fleet, live run detail, approvals v2,
certification dashboard, diff viewer, cost, ROI, health/SLO, trigger log, queue
visualizer, global search, onboarding wizard, replay, and the incident banner.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from v02_support import (
    TASK_ESCALATE,
    TASK_OK,
    canonical_identity,
    ensure_admissible,
    get,
    post,
)

pytestmark = pytest.mark.docker

API_BASE = os.environ.get("HIVEPLANE_API_URL", "http://localhost:8100").rstrip("/")
UI_BASE = os.environ.get("HIVEPLANE_UI_URL", "http://localhost:3001").rstrip("/")
ROOT = Path(__file__).resolve().parents[2]
SCREENSHOTS = ROOT / "docs" / "field-test" / "v0.2.0" / "screenshots"


def _shot(page: Page, scenario: str, step: int, name: str) -> Path:
    directory = SCREENSHOTS / scenario
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{step:02d}-{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


@pytest.fixture(scope="module")
def seeded_run() -> str:
    ensure_admissible("support-agent")
    status, run = post(
        "/runs",
        {
            "workload": "support-agent",
            "caller": "field-test-v02",
            "context": "sandbox",
            "task": dict(TASK_OK),
            "model_identity": canonical_identity(),
        },
    )
    assert status == 201, run
    post(f"/runs/{run['id']}/start")
    return str(run["id"])


@pytest.fixture(scope="module")
def seeded_approval(seeded_run: str) -> str:
    """Create a pending approval by escalating through the destructive tool."""
    status, run = post(
        "/runs",
        {
            "workload": "support-agent",
            "caller": "field-test-v02-ui",
            "context": "sandbox",
            "task": dict(TASK_ESCALATE),
            "model_identity": canonical_identity(),
        },
    )
    if status == 201:
        post(f"/runs/{run['id']}/start")
        for _ in range(30):
            _, approvals = get("/approvals")
            if isinstance(approvals, list) and approvals:
                break
            time.sleep(1)
    return seeded_run


def test_ui_v2_fleet_and_run_detail(page: Page, seeded_run: str) -> None:
    page.goto(f"{UI_BASE}/")
    expect(page.get_by_role("heading", name="Fleet")).to_be_visible()
    _shot(page, "ui-fleet", 1, "fleet")

    page.goto(f"{UI_BASE}/runs/{seeded_run}")
    expect(page.get_by_role("heading", name=f"Run {seeded_run}")).to_be_visible()
    _shot(page, "ui-run-detail", 1, "run-detail")


def test_ui_v2_approvals_and_certifications(
    page: Page, seeded_approval: str
) -> None:
    page.goto(f"{UI_BASE}/approvals")
    expect(page.get_by_role("heading", name="Approval queue")).to_be_visible()
    _shot(page, "ui-approvals", 1, "approvals")

    page.goto(f"{UI_BASE}/certifications")
    expect(page.get_by_role("heading", name="Certification dashboard")).to_be_visible()
    _shot(page, "ui-certifications", 1, "certifications")


def test_ui_v2_cost_roi_health(page: Page) -> None:
    for path, scenario, name in (
        ("/cost", "ui-cost", "cost"),
        ("/roi", "ui-roi", "roi"),
        ("/health", "ui-health", "health"),
        ("/spend", "ui-spend", "spend"),
    ):
        page.goto(f"{UI_BASE}{path}")
        expect(page.get_by_role("heading").first).to_be_visible()
        _shot(page, scenario, 1, name)


def test_ui_v2_triggers_queue_search(page: Page) -> None:
    for path, scenario, name in (
        ("/triggers", "ui-triggers", "triggers"),
        ("/queue", "ui-queue", "queue"),
        ("/search?q=repo", "ui-search", "search"),
    ):
        page.goto(f"{UI_BASE}{path}")
        expect(page.get_by_role("heading").first).to_be_visible()
        _shot(page, scenario, 1, name)


def test_ui_v2_diff_onboarding_replay(page: Page, seeded_run: str) -> None:
    page.goto(f"{UI_BASE}/diff")
    expect(page.get_by_role("heading", name="Diff")).to_be_visible()
    _shot(page, "ui-diff", 1, "diff")

    page.goto(f"{UI_BASE}/onboarding")
    expect(page.get_by_role("heading").first).to_be_visible()
    _shot(page, "ui-onboarding", 1, "onboarding")

    page.goto(f"{UI_BASE}/replay/{seeded_run}")
    expect(page.get_by_role("heading", name="Replay")).to_be_visible()
    _shot(page, "ui-replay", 1, "replay")


def test_ui_v2_incident_banner(page: Page) -> None:
    post("/fleet/pause", {"actor": "field-test-v02", "reason": "ui screenshot"})
    try:
        page.goto(f"{UI_BASE}/")
        expect(page.get_by_role("heading", name="Fleet")).to_be_visible()
        _shot(page, "ui-incident", 1, "incident-banner")
    finally:
        post("/fleet/resume", {"actor": "field-test-v02"})


def test_ui_v2_login_renders(page: Page) -> None:
    page.goto(f"{UI_BASE}/login")
    expect(page.locator("form")).to_be_visible()
    _shot(page, "ui-login", 1, "login")

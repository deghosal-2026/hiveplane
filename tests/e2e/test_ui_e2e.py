"""Browser end-to-end tests for the operator UI (M22, #91)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from e2e.live_stack import LiveStack

pytestmark = pytest.mark.e2e


def test_fleet_shows_workload(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/")

    expect(page.get_by_role("heading", name="Fleet")).to_be_visible()
    expect(page.get_by_text(stack.workload).first).to_be_visible()


def test_fleet_links_to_run_detail(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/")
    page.get_by_role("link", name=stack.run_running).click()

    expect(page.get_by_role("heading", name=f"Run {stack.run_running}")).to_be_visible()


def test_run_detail_renders_story_and_stop(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/runs/{stack.run_running}")

    expect(page.get_by_role("heading", name=f"Run {stack.run_running}")).to_be_visible()
    expect(page.get_by_text("admission").first).to_be_visible()

    page.get_by_role("button", name="Stop").click()

    expect(page.get_by_text("stop requested")).to_be_visible()


def test_approval_approve_flow(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/approvals")
    form = page.locator(f"form[action='/approvals/{stack.approval_approve}/approve']")
    form.get_by_placeholder("operator").fill("alice")

    form.get_by_role("button", name="Approve").click()

    expect(page.get_by_text("approved").first).to_be_visible()


def test_approval_deny_flow(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/approvals")
    form = page.locator(f"form[action='/approvals/{stack.approval_deny}/deny']")
    form.get_by_placeholder("operator").fill("bob")

    form.get_by_role("button", name="Deny").click()

    expect(page.get_by_text("denied").first).to_be_visible()


def test_certification_dashboard_renders(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/certifications")

    expect(page.get_by_role("heading", name="Certification dashboard")).to_be_visible()


def test_spend_view_renders(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/spend")

    expect(page.get_by_role("heading", name="Spend")).to_be_visible()


def test_unknown_run_shows_404(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/runs/does-not-exist")

    expect(page.get_by_text("No run found")).to_be_visible()


def test_control_plane_down_shows_502(page: Page, dead_ui_url: str) -> None:
    page.goto(f"{dead_ui_url}/")

    expect(page.get_by_text("Control plane unavailable")).to_be_visible()

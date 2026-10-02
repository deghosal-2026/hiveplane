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


def test_anonymous_admin_sees_nav_and_fleet_heading(page: Page, stack: LiveStack) -> None:
    """Auth-disabled deployments resolve an anonymous admin with full nav."""
    page.goto(f"{stack.ui_url}/")

    expect(page.get_by_role("heading", name="Fleet")).to_be_visible()
    expect(page.get_by_role("link", name="Fleet")).to_be_visible()
    expect(page.get_by_role("link", name="Approvals")).to_be_visible()


def test_login_form_renders(page: Page, stack: LiveStack) -> None:
    """The login screen renders its heading and API-key form."""
    page.goto(f"{stack.ui_url}/login")

    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
    expect(page.get_by_label("API key")).to_be_visible()


def test_onboarding_lists_four_steps(page: Page, stack: LiveStack) -> None:
    """The onboarding wizard renders all four step titles."""
    page.goto(f"{stack.ui_url}/onboarding")

    expect(page.get_by_role("heading", name="Onboarding")).to_be_visible()
    expect(page.get_by_role("heading", name="Connect a model")).to_be_visible()
    expect(page.get_by_role("heading", name="Register a workload")).to_be_visible()
    expect(page.get_by_role("heading", name="Certify the workload")).to_be_visible()
    expect(page.get_by_role("heading", name="Fire the first trigger")).to_be_visible()


def test_queue_shows_depth(page: Page, stack: LiveStack) -> None:
    """The queue visualizer renders its heading and a depth label."""
    page.goto(f"{stack.ui_url}/queue")

    expect(page.get_by_role("heading", name="Queue")).to_be_visible()
    expect(page.get_by_text("Depth", exact=True)).to_be_visible()


def test_health_view_renders(page: Page, stack: LiveStack) -> None:
    """The health screen renders its heading whether or not data is seeded."""
    page.goto(f"{stack.ui_url}/health")

    expect(page.get_by_role("heading", name="Health")).to_be_visible()
    expect(page.get_by_role("heading", name="Workloads")).to_be_visible()


def test_roi_view_renders(page: Page, stack: LiveStack) -> None:
    """The ROI screen renders its heading."""
    page.goto(f"{stack.ui_url}/roi")

    expect(page.get_by_role("heading", name="ROI")).to_be_visible()


def test_search_finds_seeded_workload(page: Page, stack: LiveStack) -> None:
    """Global search returns hits for the seeded workload name."""
    page.goto(f"{stack.ui_url}/search?q={stack.workload}")

    expect(page.get_by_role("heading", name="Search")).to_be_visible()
    expect(page.get_by_text(stack.workload).first).to_be_visible()


def test_diff_view_renders_empty_state(page: Page, stack: LiveStack) -> None:
    """The diff viewer renders its heading and empty state without query params."""
    page.goto(f"{stack.ui_url}/diff")

    expect(page.get_by_role("heading", name="Diff")).to_be_visible()
    expect(page.get_by_text("No diff to show.")).to_be_visible()


def test_cost_view_renders(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/cost")

    expect(page.get_by_role("heading", name="Cost")).to_be_visible()


def test_trigger_log_renders(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/triggers")

    expect(page.get_by_role("heading", name="Triggers")).to_be_visible()


def test_replay_view_renders_frames(page: Page, stack: LiveStack) -> None:
    page.goto(f"{stack.ui_url}/replay/{stack.run_running}")

    expect(page.get_by_role("heading", name="Replay")).to_be_visible()
    expect(page.get_by_text("digest").first).to_be_visible()

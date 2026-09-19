"""Shared types for browser E2E tests (M22, #91)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LiveStack:
    """URLs and seeded ids for the running E2E stack."""

    api_url: str
    ui_url: str
    workload: str
    run_running: str
    approval_approve: str
    approval_deny: str

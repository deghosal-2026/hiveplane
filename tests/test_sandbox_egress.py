"""Tests for the sandbox egress guard."""

from __future__ import annotations

import pytest

from hiveplane.core.sandbox import CLOUD_METADATA_ENDPOINTS, EgressMode, EgressSpec
from hiveplane.sandbox.egress import EgressGuard
from hiveplane.sandbox.errors import EgressDeniedError


def test_allowlisted_host_is_permitted() -> None:
    guard = EgressGuard(EgressSpec(allow=["api.openai.com"], mode=EgressMode.RESTRICTED))
    guard.check("api.openai.com")


def test_unlisted_host_is_denied() -> None:
    guard = EgressGuard(EgressSpec(allow=["api.openai.com"], mode=EgressMode.RESTRICTED))
    with pytest.raises(EgressDeniedError):
        guard.check("evil.example.com")


def test_none_mode_denies_all() -> None:
    guard = EgressGuard(EgressSpec(mode=EgressMode.NONE))
    with pytest.raises(EgressDeniedError):
        guard.check("api.openai.com")


def test_open_mode_allows_any_host() -> None:
    guard = EgressGuard(EgressSpec(mode=EgressMode.OPEN))
    guard.check("anywhere.example.com")


def test_cloud_metadata_is_always_denied() -> None:
    guard = EgressGuard(EgressSpec(mode=EgressMode.OPEN))
    with pytest.raises(EgressDeniedError):
        guard.check(CLOUD_METADATA_ENDPOINTS[0])


def test_explicit_deny_wins() -> None:
    guard = EgressGuard(
        EgressSpec(allow=["api.openai.com"], deny=["api.openai.com"], mode=EgressMode.RESTRICTED)
    )
    with pytest.raises(EgressDeniedError):
        guard.check("api.openai.com")

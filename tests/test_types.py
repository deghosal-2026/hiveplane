"""Tests for shared domain types (durations, DNS-safe names)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from hiveplane.core.types import Duration, Name

DURATION_CASES = [
    ("14d", 14 * 86400),
    ("24h", 24 * 3600),
    ("30m", 30 * 60),
    ("45s", 45),
    ("0s", 0),
    (120, 120),
    ("120", 120),
]


class _DurationBox(BaseModel):
    value: Duration


class _NameBox(BaseModel):
    value: Name


@pytest.mark.parametrize(("raw", "expected"), DURATION_CASES)
def test_duration_parses_to_seconds(raw: Any, expected: int) -> None:
    assert _DurationBox(value=raw).value == expected


@pytest.mark.parametrize("raw", ["14x", "abc", "-1d", "", "d", "1.5h"])
def test_invalid_duration_is_rejected(raw: Any) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _DurationBox(value=raw)

    assert "duration" in str(excinfo.value).lower()


@pytest.mark.parametrize("value", ["incident-agent", "a", "repo-agent-2", "x1"])
def test_valid_dns_safe_name(value: str) -> None:
    assert _NameBox(value=value).value == value


@pytest.mark.parametrize("value", ["Incident", "bad_name", "-lead", "trail-", "has space", ""])
def test_invalid_dns_safe_name_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _NameBox(value=value)

    assert "dns-safe" in str(excinfo.value).lower()


def test_name_longer_than_63_chars_is_rejected() -> None:
    with pytest.raises(ValidationError, match="63"):
        _NameBox(value="a" * 64)


@pytest.mark.parametrize("raw", [-5, True, 1.5])
def test_non_whole_or_negative_durations_are_rejected(raw: Any) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _DurationBox(value=raw)

    assert "duration" in str(excinfo.value).lower()

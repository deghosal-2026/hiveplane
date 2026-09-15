"""Shared scalar domain types: durations and DNS-safe names."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator

_DURATION_RE = re.compile(r"^(?P<amount>\d+)(?P<unit>[smhd])$")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_NAME_MAX_LENGTH = 63


def parse_duration(value: object) -> int:
    """Coerce a duration into whole seconds.

    Accepts an integer number of seconds, a numeric string, or a value with a
    unit suffix (``s``, ``m``, ``h``, ``d``) such as ``"14d"``.
    """
    if isinstance(value, bool):
        raise ValueError("duration must be a number or a duration string, not a bool")
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, float) and value.is_integer():
        seconds = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            seconds = int(text)
        else:
            match = _DURATION_RE.match(text)
            if match is None:
                raise ValueError(
                    f"invalid duration {value!r}: expected an integer number of seconds "
                    "or a value like '30s', '15m', '24h', or '14d'"
                )
            seconds = int(match.group("amount")) * _DURATION_UNITS[match.group("unit")]
    else:
        raise ValueError(
            f"invalid duration {value!r}: expected an integer number of seconds "
            "or a value like '30s', '15m', '24h', or '14d'"
        )

    if seconds < 0:
        raise ValueError(f"invalid duration {value!r}: must not be negative")
    return seconds


def validate_name(value: str) -> str:
    """Validate a DNS-safe, lower-case workload name."""
    if not value:
        raise ValueError("name must not be empty and must be DNS-safe")
    if len(value) > _NAME_MAX_LENGTH:
        raise ValueError(f"name must be at most {_NAME_MAX_LENGTH} characters (got {len(value)})")
    if _NAME_RE.match(value) is None:
        raise ValueError(
            f"name {value!r} is not DNS-safe: expected lower-case letters, digits, and "
            "hyphens, starting and ending with an alphanumeric character"
        )
    return value


Duration = Annotated[int, BeforeValidator(parse_duration)]
Name = Annotated[str, AfterValidator(validate_name)]

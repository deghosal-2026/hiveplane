"""Cron engine tests: parsing, matching, DST, and next-fire (M27-03)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from hiveplane.triggers.cron import CronError, CronExpression

_NY = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def test_parse_and_match_every_five_minutes() -> None:
    cron = CronExpression.parse("*/5 * * * *")
    assert cron.matches(datetime(2026, 1, 1, 12, 0, tzinfo=_UTC))
    assert cron.matches(datetime(2026, 1, 1, 12, 5, tzinfo=_UTC))
    assert not cron.matches(datetime(2026, 1, 1, 12, 3, tzinfo=_UTC))


def test_parse_lists_ranges_and_steps() -> None:
    cron = CronExpression.parse("0,30 9-17/2 1,15 * 1-5")
    assert cron.matches(datetime(2026, 1, 1, 9, 0, tzinfo=_UTC))  # Thursday
    assert cron.matches(datetime(2026, 1, 1, 17, 30, tzinfo=_UTC))
    assert not cron.matches(datetime(2026, 1, 1, 10, 0, tzinfo=_UTC))  # odd hour
    assert not cron.matches(datetime(2026, 1, 3, 9, 0, tzinfo=_UTC))  # Saturday, not 1/15


def test_weekday_seven_is_sunday() -> None:
    cron = CronExpression.parse("0 0 * * 7")
    assert cron.matches(datetime(2026, 1, 4, 0, 0, tzinfo=_UTC))  # Sunday
    assert not cron.matches(datetime(2026, 1, 5, 0, 0, tzinfo=_UTC))


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "* * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "*/0 * * * *",
        "a * * * *",
    ],
)
def test_invalid_expressions_raise(expression: str) -> None:
    with pytest.raises(CronError):
        CronExpression.parse(expression)


def test_next_after_returns_next_matching_minute() -> None:
    cron = CronExpression.parse("*/15 * * * *")
    after = datetime(2026, 1, 1, 12, 7, tzinfo=_UTC)
    assert cron.next_after(after) == datetime(2026, 1, 1, 12, 15, tzinfo=_UTC)


def test_next_after_respects_timezone() -> None:
    cron = CronExpression.parse("0 9 * * *")
    after = datetime(2026, 1, 1, 0, 0, tzinfo=_UTC)
    nxt = cron.next_after(after, tz=_NY)
    assert nxt is not None
    assert nxt.astimezone(_NY).hour == 9
    assert nxt.astimezone(_NY).minute == 0


def test_fall_back_repeated_hour_fires_once() -> None:
    # 2026-11-01 01:30 occurs twice in America/New_York (EDT then EST).
    cron = CronExpression.parse("30 1 * * *")
    before = datetime(2026, 10, 31, 12, 0, tzinfo=_NY)
    first = cron.next_after(before)
    assert first is not None
    second = cron.next_after(first)
    assert second is not None
    # The next fire is the following day, not the repeated 01:30.
    assert first.astimezone(_NY).date() == date(2026, 11, 1)
    assert second.astimezone(_NY).date() == date(2026, 11, 2)
    assert (second - first).total_seconds() >= 23 * 3600


def test_spring_forward_nonexistent_time_does_not_crash() -> None:
    # 2026-03-08 02:30 does not exist in America/New_York.
    cron = CronExpression.parse("30 2 * * *")
    after = datetime(2026, 3, 8, 0, 0, tzinfo=_NY)
    nxt = cron.next_after(after)
    assert nxt is not None
    assert nxt > after


def test_next_after_returns_none_when_unsatisfiable() -> None:
    cron = CronExpression.parse("0 0 30 2 *")  # February 30 never occurs
    assert cron.next_after(datetime(2026, 1, 1, tzinfo=_UTC), max_days=400) is None

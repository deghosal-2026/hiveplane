"""Minimal, strict 5-field cron parser and matcher (M27-03, D23).

Supports ``*``, single values, ranges (``a-b``), steps (``*/n``, ``a-b/n``,
``a/n``), and comma lists. Matching is wall-clock in the expression's timezone;
``next_after`` searches strictly forward so a repeated wall-clock hour (DST
fall-back) fires once and a nonexistent hour (spring-forward) does not crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

_MINUTE = (0, 59)
_HOUR = (0, 23)
_DAY = (1, 31)
_MONTH = (1, 12)
_WEEKDAY = (0, 7)  # 0 and 7 are both Sunday


class CronError(ValueError):
    """Raised when a cron expression cannot be parsed."""


@dataclass(frozen=True, slots=True)
class CronExpression:
    """A parsed five-field cron expression."""

    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]

    @classmethod
    def parse(cls, expression: str) -> CronExpression:
        """Parse a five-field cron expression, raising :class:`CronError`."""
        fields = expression.split()
        if len(fields) != 5:
            raise CronError(
                f"cron expression must have 5 fields, got {len(fields)}: {expression!r}"
            )
        minutes = _parse_field(fields[0], *_MINUTE)
        hours = _parse_field(fields[1], *_HOUR)
        days = _parse_field(fields[2], *_DAY)
        months = _parse_field(fields[3], *_MONTH)
        weekdays = frozenset(value % 7 for value in _parse_field(fields[4], *_WEEKDAY))
        return cls(
            expression=expression,
            minutes=minutes,
            hours=hours,
            days=days,
            months=months,
            weekdays=weekdays,
        )

    def matches(self, when: datetime) -> bool:
        """Return True when ``when``'s wall clock matches this expression."""
        if when.minute not in self.minutes or when.hour not in self.hours:
            return False
        if when.month not in self.months:
            return False
        return self._day_matches(when.date())

    def next_after(
        self, after: datetime, *, tz: tzinfo | None = None, max_days: int = 366
    ) -> datetime | None:
        """Return the next matching instant strictly after ``after``, or None.

        Matching is evaluated on the wall clock in ``tz`` (defaulting to
        ``after``'s timezone); the returned datetime is timezone-aware in ``tz``.
        """
        zone = tz or after.tzinfo
        local = after.astimezone(zone)
        start = (local + timedelta(minutes=1)).replace(second=0, microsecond=0)
        first_day = start.date()
        ordered_hours = sorted(self.hours)
        ordered_minutes = sorted(self.minutes)
        for offset in range(max_days + 1):
            day = first_day + timedelta(days=offset)
            if day.month not in self.months or not self._day_matches(day):
                continue
            for hour in ordered_hours:
                for minute in ordered_minutes:
                    candidate = datetime(
                        day.year, day.month, day.day, hour, minute, tzinfo=zone
                    )
                    if candidate >= start and self.matches(candidate):
                        return candidate
        return None

    def _day_matches(self, day: date) -> bool:
        dom_restricted = self.days != frozenset(range(_DAY[0], _DAY[1] + 1))
        dow_restricted = self.weekdays != frozenset(range(7))
        dom_match = day.day in self.days
        dow_match = ((day.weekday() + 1) % 7) in self.weekdays
        if dom_restricted and dow_restricted:
            return dom_match or dow_match
        if dom_restricted:
            return dom_match
        if dow_restricted:
            return dow_match
        return True


def _parse_field(text: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            raise CronError(f"empty field element in {text!r}")
        step = 1
        has_step = "/" in part
        if has_step:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"invalid step in {text!r}")
            step = int(step_text)
        if part == "*":
            values.update(range(low, high + 1, step))
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            start, end = _coerce(start_text, text), _coerce(end_text, text)
            if start > end:
                raise CronError(f"inverted range in {text!r}")
            values.update(range(start, end + 1, step))
            continue
        start = _coerce(part, text)
        if has_step:
            values.update(range(start, high + 1, step))
        else:
            values.add(start)
    if not values:
        raise CronError(f"field {text!r} matches nothing")
    for value in values:
        if not low <= value <= high:
            raise CronError(f"value {value} out of range [{low}, {high}] in {text!r}")
    return frozenset(values)


def _coerce(text: str, field: str) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise CronError(f"invalid value {text!r} in {field!r}") from exc

"""cauterule.inject() / cauterule.ainject() context managers (#538).

Both delegate to the real engine — :func:`cauterule.injection.matcher.match_rules`
for filtering and :mod:`cauterule.injection.budget` for budget-aware ordering —
so custom-loop integration matches the CLI/other adapters exactly.  Kwargs
(``tool``, ``error``, ``tags``, ``taxonomy``) are honored; ``max_rules`` /
``max_tokens`` bound the returned set.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Generator
from typing import Any

from cauterule.injection.budget import optimize_budget
from cauterule.injection.matcher import match_rules
from cauterule.models.rule import StandingRule


def _filter_and_bound(
    task: str,
    rules: list[StandingRule],
    context: dict[str, Any],
    max_rules: int | None,
    max_tokens: int | None,
) -> list[StandingRule]:
    matched = match_rules(task, rules, **context)
    if max_tokens is not None:
        matched = optimize_budget(matched, max_tokens=max_tokens)
    if max_rules is not None:
        matched = matched[:max_rules]
    return matched


@contextlib.contextmanager
def inject(
    task: str,
    rules: list[StandingRule] | None = None,
    **kwargs: Any,
) -> Generator[list[StandingRule], None, None]:
    """Context manager that yields rules matching *task*.

    Uses the real structured matcher + budget from :mod:`cauterule.injection`
    so custom loops behave identically to the CLI path (#538).

    Args:
        task: Task description to match rules against.
        rules: Optional list of promoted rules to filter.
        **kwargs: ``tool``, ``error``, ``tags``, ``taxonomy`` context
            filters plus ``max_rules`` / ``max_tokens`` budget bounds.

    Yields:
        List of matching :class:`StandingRule` objects.
    """
    if rules is None:
        yield []
        return
    max_rules = kwargs.pop("max_rules", None)
    max_tokens = kwargs.pop("max_tokens", None)
    yield _filter_and_bound(task, rules, kwargs, max_rules=max_rules, max_tokens=max_tokens)


@contextlib.asynccontextmanager
async def ainject(
    task: str,
    rules: list[StandingRule] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[list[StandingRule], None]:
    """Async variant of :func:`inject` (#538)."""
    if rules is None:
        yield []
        return
    max_rules = kwargs.pop("max_rules", None)
    max_tokens = kwargs.pop("max_tokens", None)
    yield _filter_and_bound(task, rules, kwargs, max_rules=max_rules, max_tokens=max_tokens)

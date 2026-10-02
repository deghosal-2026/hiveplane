"""Cursor pagination envelope for API v2 (M56-01).

Cursors are opaque offset tokens; a page reports the total count and the cursor
to fetch the next page (or ``None`` when exhausted).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Page[T](BaseModel):
    """A page of results with a next cursor."""

    model_config = ConfigDict(extra="forbid")

    items: list[T] = Field(default_factory=list)
    count: int = 0
    next_cursor: str | None = None


def paginate[T](items: list[T], *, limit: int, cursor: str | None) -> Page[T]:
    """Return a page of ``items`` starting at ``cursor``."""
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        start = 0
    start = max(0, start)
    window = items[start : start + limit]
    next_start = start + limit
    next_cursor = str(next_start) if next_start < len(items) else None
    return Page(items=window, count=len(items), next_cursor=next_cursor)

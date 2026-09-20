"""Shared strict coercion helpers for model ``from_dict`` parsers (#771)."""

from __future__ import annotations


def require_str_tuple(value: object, field: str) -> tuple[str, ...]:
    """Return *value* as a tuple of non-blank strings, or raise ``ValueError``.

    Guards against a scalar string silently iterating into characters (e.g.
    ``context: "nonfastforward"``), which corrupts matching/dedup/specificity
    downstream. ``None`` maps to an empty tuple.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError(  # noqa: TRY004 — issue #771 specifies ValueError
            f"'{field}' must be a list of strings, got {type(value).__name__}"
        )
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"'{field}' must contain non-blank strings")
        items.append(item)
    return tuple(items)

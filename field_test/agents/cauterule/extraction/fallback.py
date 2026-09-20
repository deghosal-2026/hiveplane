"""Fallback handler for extraction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FallbackResult:
    """Result indicating fallback to human review."""

    needs_review: bool
    reason: str


def check_fallback(
    candidate_available: bool,
    error: str | None = None,
    warnings: list[str] | None = None,
) -> FallbackResult:
    """Determine if human review is needed.

    Args:
        candidate_available: Whether a candidate was produced.
        error: Error message if extraction failed.
        warnings: Quality warnings.

    Returns:
        :class:`FallbackResult` with ``needs_review`` flag.
    """
    if not candidate_available:
        reason = error or "no candidate produced"
        return FallbackResult(needs_review=True, reason=reason)
    if warnings:
        # If any quality warning, flag for review (but not necessarily block).
        # Only hard-fail on tautological or low confidence?
        # For now, flag if warnings present and confidence low is among them.
        if any("confidence" in w for w in warnings) or any("tautological" in w for w in warnings):
            return FallbackResult(needs_review=True, reason="; ".join(warnings))
    if error:
        return FallbackResult(needs_review=True, reason=error)
    return FallbackResult(needs_review=False, reason="ok")

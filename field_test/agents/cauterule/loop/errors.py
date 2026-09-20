"""Loop error handling."""

from __future__ import annotations


def handle_extraction_error(error: Exception) -> str:
    """Handle an extraction-stage error.

    Args:
        error: The exception raised during extraction.

    Returns:
        A user-facing message describing the error and suggested action.
    """
    return (
        f"Rule extraction failed: {error}. "
        "The trajectory could not be parsed into a candidate rule. "
        "Consider manual review or adjusting the LLM configuration."
    )


def handle_replay_error(error: Exception) -> str:
    """Handle a replay-stage error.

    Args:
        error: The exception raised during replay.

    Returns:
        A user-facing message describing the error and suggested action.
    """
    return (
        f"Replay evaluation failed: {error}. "
        "The candidate rule could not be tested against historical data. "
        "The replay cache or historical index may be corrupted."
    )


def handle_linter_block(warnings: list[str]) -> str:
    """Handle a linter block — rule was rejected by the linter.

    Args:
        warnings: Linter warnings that caused the block.

    Returns:
        A user-facing message describing why the rule was blocked.
    """
    bullet = "\n- ".join(warnings)
    return (
        f"The candidate rule was blocked by the linter with {len(warnings)} warning(s):\n"
        f"- {bullet}\n"
        "Review the warnings above and refine the rule before retrying."
    )


def handle_git_error(error: Exception) -> str:
    """Handle a git-related error during promotion.

    Args:
        error: The exception raised during a git operation.

    Returns:
        A user-facing message describing the error and suggested action.
    """
    return (
        f"Git operation failed during promotion: {error}. "
        "The rule may have been extracted but could not be committed. "
        "Check repository permissions and git status."
    )

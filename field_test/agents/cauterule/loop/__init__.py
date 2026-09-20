"""Loop orchestration package — capture, extract, promote, inject."""

from __future__ import annotations

from cauterule.loop.errors import (
    handle_extraction_error,
    handle_git_error,
    handle_linter_block,
    handle_replay_error,
)
from cauterule.loop.orchestrator import LoopConfig, run_loop

__all__ = [
    "LoopConfig",
    "handle_extraction_error",
    "handle_git_error",
    "handle_linter_block",
    "handle_replay_error",
    "run_loop",
]

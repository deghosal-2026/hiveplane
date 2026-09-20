"""Logging provider wrapper — captures every prompt and raw LLM response.

Wraps any LLMProvider to save the full prompt (system + user messages) and
the raw completion to a per-goal log file. Used by the field test harness so
every LLM interaction is auditable and replayable.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path

from .base import Completion, LLMProvider, Message, ToolSchema

logger = logging.getLogger(__name__)


class LoggingProvider:
    """Wraps an LLMProvider, saving every prompt + response to a log file."""

    def __init__(
        self,
        inner: LLMProvider,
        log_dir: Path,
        goal_id: str,
        role: str = "unknown",
    ) -> None:
        self._inner = inner
        self._log_dir = Path(log_dir)
        self._goal_id = goal_id
        self._role = role
        self._call_count = 0
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / f"{goal_id}_{role}_llm_log.jsonl"

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def base_url(self) -> str:
        return self._inner.base_url

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(
        self,
        messages: Sequence[Message],
        tool_schemas: Sequence[ToolSchema] = (),
    ) -> Completion:
        """Call the inner provider and log the prompt + response."""
        self._call_count += 1
        call_num = self._call_count
        start = time.monotonic()

        # Serialize the prompt
        prompt_data = [
            {"role": m.role, "content": m.content, "content_len": len(m.content)} for m in messages
        ]
        prompt_text = "\n\n".join(f"--- {m.role} ---\n{m.content}" for m in messages)

        # Call the inner provider
        error = None
        try:
            completion = self._inner.complete(messages, tool_schemas)
            response_content = completion.content
            finish_reason = completion.finish_reason
        except Exception as e:
            completion = None
            response_content = None
            finish_reason = None
            error = f"{type(e).__name__}: {e}"

        duration = round(time.monotonic() - start, 2)

        # Build the log entry
        entry = {
            "call": call_num,
            "goal_id": self._goal_id,
            "role": self._role,
            "model": self._inner.model,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": duration,
            "prompt_messages": prompt_data,
            "prompt_full_text": prompt_text,
            "prompt_total_chars": sum(len(m.content) for m in messages),
            "response_content": response_content,
            "response_chars": len(response_content) if response_content else 0,
            "finish_reason": finish_reason,
            "error": error,
        }

        # Append to the JSONL log file
        with self._log_path.open("a") as fh:
            fh.write(json.dumps(entry, indent=2, default=str) + "\n")

        if error:
            raise RuntimeError(error)

        return completion

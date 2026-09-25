from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from field_test.shims import support_agent


class _FakeCtx:
    def __init__(self, shaped: Any) -> None:
        self._shaped = shaped
        self.calls: list[str] = []

    def tool_call(self, tool_id: str, **kwargs: Any) -> Any:
        self.calls.append(tool_id)
        return SimpleNamespace(shaped_output=self._shaped)


def test_large_task_reports_shaped_truncation() -> None:
    shaped = SimpleNamespace(truncated=True, original_bytes=40001, shaped_bytes=16384)
    ctx = _FakeCtx(shaped)

    result = support_agent.run({"large": True}, ctx)  # type: ignore[arg-type]

    assert ctx.calls == ["mcp.github.read_large_issue"]
    assert result["truncated"] is True
    assert result["original_bytes"] == 40001
    assert result["shaped_bytes"] == 16384


def test_large_task_reports_when_not_truncated() -> None:
    shaped = SimpleNamespace(truncated=False, original_bytes=100, shaped_bytes=100)
    ctx = _FakeCtx(shaped)

    result = support_agent.run({"large": True}, ctx)  # type: ignore[arg-type]

    assert result["truncated"] is False
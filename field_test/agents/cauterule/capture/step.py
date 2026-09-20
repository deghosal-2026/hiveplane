"""Step capture helpers."""

from __future__ import annotations

from typing import Any

from cauterule.models.trajectory import Step


def create_step(
    step_number: int,
    tool: str,
    input: str | None = None,
    output: str | None = None,
    error: str | None = None,
    state: dict[str, Any] | None = None,
) -> Step:
    """Create a :class:`Step` with validation.

    Args:
        step_number: 1-indexed step number.
        tool: Tool name.
        input: Tool input.
        output: Tool output.
        error: Error message if any.
        state: Optional agent state snapshot.
    """
    return Step(
        step_number=step_number,
        tool=tool,
        input=input,
        output=output,
        error=error,
        state=state,
    )


class StepCollector:
    """Collect steps sequentially."""

    def __init__(self) -> None:
        self._steps: list[Step] = []

    def add(
        self,
        tool: str,
        input: str | None = None,
        output: str | None = None,
        error: str | None = None,
        state: dict[str, Any] | None = None,
    ) -> Step:
        """Add a step and return it."""
        step = create_step(len(self._steps) + 1, tool, input, output, error, state)
        self._steps.append(step)
        return step

    def steps(self) -> tuple[Step, ...]:
        """Return collected steps."""
        return tuple(self._steps)

    def clear(self) -> None:
        """Clear collected steps."""
        self._steps.clear()

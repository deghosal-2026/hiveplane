"""CrewAI adapter — task failure capture + rule injection (#536).

Duck-typed: no hard ``crewai`` dependency.  Provides:

- :class:`CrewaiTracer` — records a crew task run as a trajectory (per-tool
  steps) and can be used as an explicit wrapper or as callback listeners
  (``on_tool_error`` / ``on_task_complete``).
- :func:`inject_crew_rules` — rendered rule text block to append to a CrewAI
  ``Task(description=...)``.
"""

from __future__ import annotations

import importlib.util
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from cauterule.adapter.inject import inject
from cauterule.capture.failure import detect_failure_class, detect_failure_point
from cauterule.capture.metadata import enrich_trajectory
from cauterule.capture.step import StepCollector
from cauterule.capture.writer import write_trajectory
from cauterule.models.rule import StandingRule
from cauterule.models.trajectory import Trajectory
from cauterule.redaction.engine import redact_trajectory

HAS_CREWAI = importlib.util.find_spec("crewai") is not None


def inject_crew_rules(
    task_desc: str,
    *,
    tools: list[str] | None = None,
    error_hint: str | None = None,
    rules: list[StandingRule] | None = None,
) -> str:
    """Return a rendered rule block to append to a CrewAI task description.

    Args:
        task_desc: Task description to match rules against.
        tools: Tool names to use as a context filter.
        error_hint: Optional error hint to narrow matching.
        rules: Standing rules; defaults to the store's active rules.

    Returns:
        A text block beginning with a header and one rule per line; empty
        string when no rules match.
    """
    if rules is None:
        from cauterule.store.manager import StoreManager

        rules = StoreManager().list_rules(status="active")
    kwargs: dict[str, Any] = {}
    if tools:
        kwargs["tags"] = tools
    if error_hint:
        kwargs["error"] = error_hint
    with inject(task_desc, rules=rules, **kwargs) as matched:
        lines = [f"{r.id}: when {r.when.trigger} do {r.do.directive}" for r in matched]
    if not lines:
        return ""
    return "Standing rules (learned from past failures):\n" + "\n".join(
        f"{i + 1}. {line}" for i, line in enumerate(lines)
    )


@dataclass
class CrewaiTracer:
    """Trace a CrewAI task run into a CauterRule trajectory (#536).

    Two integration styles:
    - explicit wrapper: ``with tracer.task(desc, agent_role, tools): crew.kickoff()``
    - as callback listeners: register ``tracer.on_tool_error`` /
      ``tracer.on_task_complete`` with the crew.

    A single trajectory is written per task completion (success or error).
    """

    task_desc: str = ""
    base_dir: str = "trajectories"
    steps: list[dict[str, Any]] = field(default_factory=list)
    task_error: str | None = None
    _last: Trajectory | None = field(default=None, repr=False)

    def task(
        self,
        task_desc: str,
        agent_role: str = "",
        tools_used: list[str] | None = None,
    ) -> Any:
        """Context manager wrapping one crew task execution."""

        from contextlib import contextmanager

        @contextmanager
        def _ctx() -> Any:
            self.task_desc = task_desc
            self.steps = []
            self.task_error = None
            import time as _t

            start = _t.time()
            try:
                msg = {"agent_role": agent_role, "tools_used": tools_used or []}
                yield self
                self._add_step("task", str(msg)[:2000], output="ok", duration=_t.time() - start)
                self.on_task_complete()
            except Exception as exc:
                self.task_error = str(exc)
                self._add_step(
                    "task",
                    str({"task": task_desc})[:2000],
                    error=str(exc),
                    duration=_t.time() - start,
                )
                self.on_tool_error(task_desc, exc)
                raise
            finally:
                self._flush()

        return _ctx()

    def record_tool(
        self, tool: str, input_: str = "", output: str = "", error: str | None = None
    ) -> None:
        """Record one tool invocation within the current task."""
        if error:
            self._add_step(tool, input_, error=error)
        else:
            self._add_step(tool, input_, output=output)

    # -- callback-compatible listener API -----------------------------------
    def on_tool_error(self, _tool: str, exception: Exception) -> None:  # pragma: no cover
        """CrewAI-compatible callback: record a tool error."""
        self.task_error = str(exception)

    def on_task_complete(self) -> None:  # pragma: no cover
        """CrewAI-compatible callback: mark the task complete (no-op; flush on exit)."""
        pass

    # -- internals ----------------------------------------------------------
    def _add_step(
        self, tool: str, input_: str, output: str = "", error: str = "", duration: float = 0.0
    ) -> None:
        self.steps.append(
            {
                "tool": tool,
                "input": input_,
                "output": output,
                "error": error,
                "duration": duration,
            }
        )

    def _flush(self) -> Trajectory | None:
        if not self.steps:
            return None
        collector = StepCollector()
        for s in self.steps:
            if s.get("error"):
                collector.add(tool=s["tool"], input=s.get("input", ""), error=s["error"])
            else:
                collector.add(tool=s["tool"], input=s.get("input", ""), output=s.get("output", ""))
        steps = collector.steps()
        success = self.task_error is None
        traj = Trajectory(
            id=f"T-{uuid.uuid4().hex[:8]}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            task=self.task_desc,
            steps=steps,
            success=success,
            failure_point=detect_failure_point(steps),
            failure_class=detect_failure_class(steps),
        )
        traj = enrich_trajectory(traj)
        traj = redact_trajectory(traj)
        write_trajectory(traj, base_dir=self.base_dir)
        self._last = traj
        self.steps = []
        return traj

"""Trajectory and Step data models.

Schema per ``docs/design/trajectory-schema-design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from cauterule.log import get_logger
from cauterule.models._coercion import require_str_tuple

_log = get_logger(__name__)

QualityLabel = Literal[
    "clear", "noisy", "ambiguous", "multi-causal", "misleading", "operator-induced", "open-ended"
]
Severity = Literal["low", "medium", "high"]
ExpectedOutcome = Literal["should_extract", "should_silence", "should_reject"]
ExpectedOutcomeConfidence = Literal["high", "medium", "low", None]

_VALID_QUALITY_LABELS: frozenset[str] = frozenset(
    {"clear", "noisy", "ambiguous", "multi-causal", "misleading", "operator-induced", "open-ended"}
)
_VALID_SEVERITIES: frozenset[str] = frozenset({"low", "medium", "high"})
_VALID_EXPECTED_OUTCOMES: frozenset[str] = frozenset(
    {"should_extract", "should_silence", "should_reject"}
)


def _require_nonblank(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


_TRUE_STRINGS = frozenset({"true", "1"})
_FALSE_STRINGS = frozenset({"false", "0"})


def _coerce_bool(value: object, field: str) -> bool:
    """Strictly coerce a boolean-ish JSON value (#769).

    Plain ``bool()`` inverts ``"false"``/``"0"`` to ``True`` — a failure
    trajectory would be silently relabelled a success. Accept only real
    booleans, integer ``0``/``1``, and the strings ``"true"``/``"false"``
    (case/whitespace-insensitive); reject everything else loud.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    raise ValueError(f"Trajectory field '{field}' must be a boolean, got {value!r}")


@dataclass(frozen=True)
class AgentConfig:
    """Agent configuration at time of execution."""

    model: str | None = None
    tools: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {}
        if self.model is not None:
            d["model"] = self.model
        if self.tools:
            d["tools"] = list(self.tools)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentConfig:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(model=data.get("model"), tools=require_str_tuple(data.get("tools", []), "tools"))


@dataclass(frozen=True)
class Environment:
    """Environment context for a trajectory."""

    os: str | None = None
    ci: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {}
        if self.os is not None:
            d["os"] = self.os
        if self.ci is not None:
            d["ci"] = self.ci
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Environment:
        """Create from a dict produced by :meth:`to_dict`."""
        return cls(os=data.get("os"), ci=data.get("ci"))


@dataclass(frozen=True)
class Step:
    """A single step within a trajectory."""

    step_number: int
    tool: str
    input: str | None = None
    output: str | None = None
    error: str | None = None
    state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.step_number < 1:
            raise ValueError(f"step_number must be >=1, got {self.step_number}")
        _require_nonblank(self.tool, "step.tool")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {"step_number": self.step_number, "tool": self.tool}
        if self.input is not None:
            d["input"] = self.input
        if self.output is not None:
            d["output"] = self.output
        if self.error is not None:
            d["error"] = self.error
        if self.state is not None:
            d["state"] = self.state
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_index: int | None = None) -> Step:
        """Create from a dict produced by :meth:`to_dict`.

        Args:
            data: Step mapping. Must contain ``step_number`` unless
                *default_index* is given.
            default_index: Zero-based position of this step in its trajectory.
                Used to auto-number steps missing ``step_number`` (#595);
                a warning is logged when this fallback fires.

        Raises:
            ValueError: If ``step_number`` is missing and no *default_index*
                was provided (fail loud instead of collapsing to step 1).
        """
        if "step_number" not in data:
            if default_index is None:
                raise ValueError("Step missing required 'step_number' field")
            _log.warning(
                "step missing step_number; auto-numbering by position",
                extra={"default_index": default_index},
            )
            step_number = default_index + 1
        else:
            step_number = int(data["step_number"])
        return cls(
            step_number=step_number,
            tool=data.get("tool", ""),
            input=data.get("input"),
            output=data.get("output"),
            error=data.get("error"),
            state=data.get("state"),
        )


@dataclass(frozen=True)
class Trajectory:
    """An execution trajectory with steps and metadata."""

    id: str
    timestamp: str
    task: str
    steps: tuple[Step, ...]
    success: bool
    failure_point: str | None = None
    failure_class: str | None = None
    quality_label: QualityLabel | None = None
    domain: str | None = None
    severity: Severity | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    agent_config: AgentConfig | None = None
    environment: Environment | None = None
    redacted: bool = False
    injection_signal: bool = False
    expected_outcome: ExpectedOutcome | None = None
    expected_outcome_rationale: str | None = None
    expected_outcome_confidence: ExpectedOutcomeConfidence = None
    expected_rule: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.id, "trajectory.id")
        _require_nonblank(self.timestamp, "trajectory.timestamp")
        _require_nonblank(self.task, "trajectory.task")
        if self.quality_label is not None and self.quality_label not in _VALID_QUALITY_LABELS:
            raise ValueError(f"quality_label must be one of {_VALID_QUALITY_LABELS}")
        if self.severity is not None and self.severity not in _VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {_VALID_SEVERITIES}")
        if (
            self.expected_outcome is not None
            and self.expected_outcome not in _VALID_EXPECTED_OUTCOMES
        ):
            raise ValueError(f"expected_outcome must be one of {_VALID_EXPECTED_OUTCOMES}")
        for t in self.tags:
            _require_nonblank(t, "tags item")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        d: dict[str, Any] = {
            "trajectory_id": self.id,
            "timestamp": self.timestamp,
            "task": self.task,
            "steps": [s.to_dict() for s in self.steps],
            "success": self.success,
            "redacted": self.redacted,
        }
        if self.failure_point is not None:
            d["failure_point"] = self.failure_point
        if self.failure_class is not None:
            d["failure_class"] = self.failure_class
        if self.quality_label is not None:
            d["quality_label"] = self.quality_label
        if self.domain is not None:
            d["domain"] = self.domain
        if self.severity is not None:
            d["severity"] = self.severity
        if self.tags:
            d["tags"] = list(self.tags)
        if self.agent_config is not None:
            d["agent_config"] = self.agent_config.to_dict()
        if self.environment is not None:
            d["environment"] = self.environment.to_dict()
        if self.injection_signal:
            d["injection_signal"] = True
        if self.expected_outcome is not None:
            d["expected_outcome"] = self.expected_outcome
        if self.expected_outcome_rationale is not None:
            d["expected_outcome_rationale"] = self.expected_outcome_rationale
        if self.expected_outcome_confidence is not None:
            d["expected_outcome_confidence"] = self.expected_outcome_confidence
        if self.expected_rule is not None:
            d["expected_rule"] = self.expected_rule
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trajectory:
        """Create from a dict produced by :meth:`to_dict`.

        Raises:
            ValueError: If the required ``success`` field is absent (#594 —
                a missing flag must not silently label a success as failure).
        """
        if "success" not in data or data.get("success") is None:
            raise ValueError("Trajectory missing required 'success' field")
        # Accept both 'id' and 'trajectory_id' for flexibility.
        tid = data.get("trajectory_id") or data.get("id", "")
        steps_raw = data.get("steps", [])
        steps = tuple(
            Step.from_dict(s, default_index=i)
            for i, s in enumerate(steps_raw)
            if isinstance(s, dict)
        )
        ac_raw = data.get("agent_config")
        env_raw = data.get("environment")
        return cls(
            id=str(tid),
            timestamp=str(data.get("timestamp", "")),
            task=str(data.get("task", "")),
            steps=steps,
            success=_coerce_bool(data["success"], "success"),
            failure_point=data.get("failure_point"),
            failure_class=data.get("failure_class"),
            quality_label=data.get("quality_label"),
            domain=data.get("domain"),
            severity=data.get("severity"),
            tags=require_str_tuple(data.get("tags", []), "tags"),
            agent_config=AgentConfig.from_dict(ac_raw) if isinstance(ac_raw, dict) else None,
            environment=Environment.from_dict(env_raw) if isinstance(env_raw, dict) else None,
            redacted=_coerce_bool(data.get("redacted", False), "redacted"),
            injection_signal=_coerce_bool(data.get("injection_signal", False), "injection_signal"),
            expected_outcome=data.get("expected_outcome"),
            expected_outcome_rationale=data.get("expected_outcome_rationale"),
            expected_outcome_confidence=data.get("expected_outcome_confidence"),
            expected_rule=data.get("expected_rule"),
        )

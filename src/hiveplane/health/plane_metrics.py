"""In-process plane self-metrics in Prometheus text format (M43-06).

Deliberately decoupled: the registry holds no database or health-service
dependency, so a failing control-plane dependency cannot blind the plane.
"""

from __future__ import annotations

from collections import defaultdict


class PlaneMetrics:
    """A tiny thread-unsafe counter/gauge registry rendering Prometheus text."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(
            float
        )
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    @staticmethod
    def _labels(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(labels.items()))

    def increment(
        self, name: str, *, amount: float = 1.0, **labels: str
    ) -> None:
        """Increment a counter."""
        key = (name, self._labels(labels))
        self._counters[key] += amount

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        """Set a gauge value."""
        self._gauges[(name, self._labels(labels))] = value

    def render(self) -> str:
        """Render the current metrics as Prometheus text exposition."""
        lines: list[str] = []
        for (name, labels), value in sorted(self._counters.items()):
            lines.append(f"{name}{_render_labels(labels)} {_render_number(value)}")
        for (name, labels), value in sorted(self._gauges.items()):
            lines.append(f"{name}{_render_labels(labels)} {_render_number(value)}")
        return "\n".join(lines) + ("\n" if lines else "")


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + inner + "}"


def _render_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)

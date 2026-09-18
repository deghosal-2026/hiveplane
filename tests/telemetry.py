"""In-memory span capture for telemetry tests (M19, #48)."""

from __future__ import annotations

from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class SpanRecorder:
    """Captures finished spans from a private tracer provider."""

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = provider.get_tracer("hiveplane-test")

    @property
    def spans(self) -> Sequence[ReadableSpan]:
        """Return every span finished so far, oldest first."""
        return self.exporter.get_finished_spans()

    def names(self) -> list[str]:
        """Return the names of every finished span."""
        return [span.name for span in self.spans]

    def find(self, name: str) -> ReadableSpan:
        """Return the most recent span with ``name`` or fail with context."""
        matches = [span for span in self.spans if span.name == name]
        assert matches, f"no span named {name!r}; got {self.names()}"
        return matches[-1]

    def find_all(self, name: str) -> list[ReadableSpan]:
        """Return every span with ``name``, oldest first."""
        return [span for span in self.spans if span.name == name]

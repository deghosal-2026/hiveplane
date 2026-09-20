"""OpenTelemetry exporter — emit rule events as OTel spans (#588).

Span taxonomy: ``rule.match``, ``rule.promote``, ``rule.retire``,
``replay.verdict``. Ships to any OTLP/HTTP collector; fully inert when
disabled or when the OTel packages are absent (never raises into the
rule pipeline).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from cauterule.log import get_logger

_log = get_logger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTEL_AVAILABLE = False


AttributeValue = (
    str | bool | int | float | Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]
)

_CONFIGURED = False


def _ensure_configured() -> None:
    """Lazily configure the OTLP provider from ``[otel]`` on first emit."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    try:
        from cauterule.config import load_config

        cfg = load_config().otel
    except Exception:
        return
    if cfg.enabled and cfg.endpoint:
        configure_otlp(cfg.endpoint, cfg.service_name, dict(cfg.headers))


def _coerce_attribute(value: object) -> AttributeValue:
    """Coerce *value* to an OTel-safe attribute type (#508).

    Passes through ``str``/``bool``/``int``/``float`` (and homogeneous
    sequences thereof); coerces anything else with ``str()`` so telemetry
    can never crash the caller. ``bool`` is checked before ``int``
    (``bool`` subclasses ``int``) but passes through unchanged either way.
    """
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        # OTel sequences must be homogeneous; mixed or complex content
        # is stringified instead of crashing the caller.
        kinds = {type(v) for v in value}
        if not kinds or kinds <= {str} or kinds <= {bool} or kinds <= {int, float}:
            return cast(AttributeValue, list(value))
    return str(value)


class OtelExporter:
    """Emits Cauterule rule events as OpenTelemetry spans.

    Each exporter owns its own :class:`TracerProvider` — this module never
    mutates the global ``opentelemetry.trace`` provider, so a collector
    configured for one use can never leak into unrelated code paths
    (#588). If the ``opentelemetry-api`` package is not installed, all
    methods are no-ops.

    Args:
        service_name: Service name for the tracer (default ``"cauterule"``).
        endpoint: OTLP/HTTP endpoint; when provided the exporter wires its own
            batch processor. When empty, emits into a no-op tracer.
    """

    def __init__(
        self,
        service_name: str = "cauterule",
        endpoint: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.service_name = service_name
        self._provider: Any = None
        self._tracer: Any | None = None
        if _OTEL_AVAILABLE:
            try:
                if endpoint:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter,
                    )
                    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
                    from opentelemetry.sdk.trace import TracerProvider
                    from opentelemetry.sdk.trace.export import BatchSpanProcessor

                    resource = Resource.create({SERVICE_NAME: service_name})
                    self._provider = TracerProvider(resource=resource)
                    self._provider.add_span_processor(
                        BatchSpanProcessor(
                            OTLPSpanExporter(endpoint=endpoint, headers=headers or {})
                        )
                    )
                    self._tracer = self._provider.get_tracer(service_name)
                else:
                    # No endpoint: the API-only tracer is enough — do not import
                    # the OTLP exporter/SDK (the `[otel]` exporter extra may be
                    # absent). Regression: the exporter import used to run
                    # unconditionally and disabled the no-op tracer.
                    self._tracer = trace.get_tracer(service_name)
            except Exception:
                _log.warning("OTel tracer setup failed — export disabled", exc_info=True)
                self._tracer = None
        else:
            # Actionable hint, logged once per exporter (#507) — the
            # no-op path is otherwise completely silent.
            _log.warning(
                "opentelemetry-api is not installed — OTel export is disabled. "
                "pip install cauterule[otel] to enable it."
            )

    def flush(self, timeout_ms: int = 5000) -> None:
        """Flush pending spans to the collector (best effort; never raises)."""
        if self._provider is None:
            return
        try:
            self._provider.force_flush(timeout_millis=timeout_ms)
        except Exception:
            _log.warning("OTel flush failed — continuing", exc_info=True)

    def emit_rule_hit(self, rule_id: str, context: dict[str, Any] | None = None) -> None:
        """Record a rule-hit event as an OTel span.

        Args:
            rule_id: The identifier of the rule that was hit.
            context: Optional attributes to attach to the span.
        """
        if not _OTEL_AVAILABLE or self._tracer is None:
            return
        with self._tracer.start_as_current_span(
            "rule.hit",
            kind=SpanKind.INTERNAL,
        ) as span:
            span.set_attribute("rule.id", rule_id)
            if context:
                for k, v in context.items():
                    span.set_attribute(k, _coerce_attribute(v))

    def emit_rule_promotion(self, rule_id: str, title: str | None = None) -> None:
        """Record a rule-promotion event as an OTel span.

        Args:
            rule_id: The identifier of the promoted rule.
            title: Optional human-readable title.
        """
        if not _OTEL_AVAILABLE or self._tracer is None:
            return
        with self._tracer.start_as_current_span(
            "rule.promotion",
            kind=SpanKind.INTERNAL,
        ) as span:
            span.set_attribute("rule.id", rule_id)
            span.set_status(Status(StatusCode.OK))
            if title:
                span.set_attribute("rule.title", title)

    def emit_rule_extraction(
        self,
        rule_id: str,
        trajectory: str | None = None,
        accuracy: float | None = None,
    ) -> None:
        """Record a rule-extraction event as an OTel span.

        Args:
            rule_id: The identifier of the extracted rule.
            trajectory: Optional source trajectory identifier.
            accuracy: Optional extraction accuracy score.
        """
        if not _OTEL_AVAILABLE or self._tracer is None:
            return
        with self._tracer.start_as_current_span(
            "rule.extraction",
            kind=SpanKind.INTERNAL,
        ) as span:
            span.set_attribute("rule.id", rule_id)
            if trajectory:
                span.set_attribute("rule.trajectory", trajectory)
            if accuracy is not None:
                span.set_attribute("rule.accuracy", accuracy)

    # --- v0.3.0 span taxonomy (#588) ---

    def _span(self, name: str, attributes: dict[str, Any]) -> None:
        _ensure_configured()
        if not _OTEL_AVAILABLE or self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span(name, kind=SpanKind.INTERNAL) as span:
                for key, value in attributes.items():
                    span.set_attribute(key, _coerce_attribute(value))
                span.set_status(Status(StatusCode.OK))
        except Exception:
            _log.warning("OTel emit failed for span %s — continuing", name)

    def emit_rule_match(
        self,
        rule_id: str,
        agent: str = "",
        trigger: str = "",
        confidence: float = 0.0,
        match_type: str = "trigger",
    ) -> None:
        """Emit a ``rule.match`` span."""
        self._span(
            "rule.match",
            {
                "rule_id": rule_id,
                "agent": agent,
                "trigger": trigger,
                "confidence": confidence,
                "match_type": match_type,
            },
        )

    def emit_rule_promote(
        self,
        rule_id: str,
        from_state: str = "candidate",
        to_state: str = "active",
        evidence_id: str = "",
        justification: str = "",
    ) -> None:
        """Emit a ``rule.promote`` span."""
        self._span(
            "rule.promote",
            {
                "rule_id": rule_id,
                "from_state": from_state,
                "to_state": to_state,
                "evidence_id": evidence_id,
                "justification": justification,
            },
        )

    def emit_rule_retire(
        self, rule_id: str, reason: str = "stale", superseded_by: str = ""
    ) -> None:
        """Emit a ``rule.retire`` span."""
        self._span(
            "rule.retire",
            {
                "rule_id": rule_id,
                "reason": reason,
                "superseded_by": superseded_by,
            },
        )

    def emit_replay_verdict(
        self,
        evidence_id: str = "",
        verdict: str = "",
        precision_score: float = 0.0,
        recall_score: float = 0.0,
    ) -> None:
        """Emit a ``replay.verdict`` span."""
        self._span(
            "replay.verdict",
            {
                "evidence_id": evidence_id,
                "verdict": verdict,
                "precision_score": precision_score,
                "recall_score": recall_score,
            },
        )


def configure_otlp(
    endpoint: str,
    service_name: str = "cauterule",
    headers: dict[str, str] | None = None,
) -> OtelExporter:
    """Build a standalone exporter wired to an OTLP/HTTP collector.

    Does **not** touch the global tracer provider — the returned exporter
    owns an isolated batch processor, so this is safe to call anywhere.
    Returns a metadata-only exporter when the SDK packages are absent.
    Never raises.
    """
    try:
        return OtelExporter(service_name=service_name, endpoint=endpoint, headers=headers)
    except Exception:
        _log.warning("OTLP configuration failed for %s — continuing disabled", endpoint)
        return OtelExporter(service_name=service_name)


_exporter: OtelExporter | None = None


def get_exporter() -> OtelExporter:
    """Return the module singleton, lazily configured from ``[otel]`` config.

    Never raises and never leaks a collector: when telemetry is disabled or
    unconfigured, the singleton emits into a no-op tracer.
    """
    global _exporter
    if _exporter is not None:
        return _exporter
    try:
        from cauterule.config import load_config

        cfg = load_config().otel
    except Exception:
        cfg = None
    if cfg is not None and cfg.enabled and cfg.endpoint:
        _exporter = OtelExporter(
            service_name=cfg.service_name,
            endpoint=cfg.endpoint,
            headers=dict(cfg.headers),
        )
    else:
        _exporter = OtelExporter(service_name=cfg.service_name if cfg else "cauterule")
    return _exporter

"""OpenTelemetry bootstrap, run correlation, and span helpers (DD-06).

HivePlane is OpenTelemetry-native: traces, metrics, logs, and audit events are
correlated by run so a single execution story can be followed across signals.
This module owns the process-wide tracer provider and the small vocabulary of
attributes (``run_id``, ``workload``, ``team``) every span carries.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import copy_context

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind, Tracer
from opentelemetry.util.types import AttributeValue
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hiveplane.config import OtelSettings
from hiveplane.core.run import Run
from hiveplane.core.workload import AgentWorkload

#: Instrumentation scope name for every HivePlane span.
TRACER_NAME = "hiveplane"

#: Correlation attributes shared by traces, metrics, and logs.
RUN_ID = "run_id"
WORKLOAD = "workload"
TEAM = "team"
CERTIFICATION_STATUS = "certification_status"
MODEL_IDENTITY = "model_identity"

_provider: TracerProvider | None = None


def get_tracer() -> Tracer:
    """Return the HivePlane tracer from the active (or no-op) provider."""
    return trace.get_tracer(TRACER_NAME)


def run_attributes(
    run: Run, workload: AgentWorkload | None = None
) -> dict[str, AttributeValue]:
    """Build the correlation attributes for a run, enriching from its workload."""
    attributes: dict[str, AttributeValue] = {RUN_ID: run.id, WORKLOAD: run.workload_id}
    if workload is not None:
        attributes[WORKLOAD] = workload.name
        if workload.team is not None:
            attributes[TEAM] = workload.team
        attributes[CERTIFICATION_STATUS] = workload.certification_status.value
    if run.model_identity is not None:
        attributes[MODEL_IDENTITY] = run.model_identity
    return attributes


@contextmanager
def span(
    name: str,
    *,
    run: Run | None = None,
    workload: AgentWorkload | None = None,
    attributes: Mapping[str, AttributeValue] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    context: Context | None = None,
) -> Iterator[Span]:
    """Start a span, seeding it with run correlation and any extra attributes."""
    merged: dict[str, AttributeValue] = {}
    if run is not None:
        merged.update(run_attributes(run, workload))
    if attributes is not None:
        merged.update(attributes)
    with get_tracer().start_as_current_span(
        name, attributes=merged, kind=kind, context=context
    ) as active:
        yield active


class TelemetryMiddleware:
    """ASGI middleware that wraps every HTTP request in a server span."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Start a ``METHOD path`` span and record the response status."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method = scope["method"]
        path = scope["path"]
        carrier = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope["headers"]
        }
        with span(
            f"{method} {path}",
            kind=SpanKind.SERVER,
            attributes={"http.request.method": method, "http.route": path},
            context=extract(carrier),
        ) as active:

            async def _send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    active.set_attribute("http.response.status_code", message["status"])
                await send(message)

            await self._app(scope, receive, _send)


def propagate_context(work: Callable[[], None]) -> Callable[[], None]:
    """Bind ``work`` to the current context so spans nest across a thread hop.

    Adapters run entrypoints on background threads. Python context (including the
    active OpenTelemetry span) does not cross threads automatically, so the
    spawner must run the work inside a copy of the context captured here.
    """
    context = copy_context()

    def _run() -> None:
        context.run(work)

    return _run


def build_tracer_provider(
    settings: OtelSettings, *, exporter: SpanExporter | None = None
) -> TracerProvider:
    """Build a tracer provider for the configured service and sampling rate."""
    resource = Resource.create({"service.name": settings.service_name})
    sampler = ParentBased(TraceIdRatioBased(settings.trace_sampling))
    provider = TracerProvider(resource=resource, sampler=sampler)
    provider.add_span_processor(BatchSpanProcessor(exporter or _build_exporter(settings)))
    return provider


def configure_telemetry(
    settings: OtelSettings, *, exporter: SpanExporter | None = None
) -> TracerProvider:
    """Install the process-wide tracer provider, once, and return it.

    Idempotent so the application factory and test harnesses can call it freely.
    """
    global _provider
    if _provider is not None:
        return _provider
    provider = build_tracer_provider(settings, exporter=exporter)
    trace.set_tracer_provider(provider)
    _provider = provider
    return provider


def _build_exporter(settings: OtelSettings) -> SpanExporter:
    """Build the OTLP/HTTP trace exporter for the configured endpoint."""
    return OTLPSpanExporter(endpoint=f"{settings.endpoint.rstrip('/')}/v1/traces")

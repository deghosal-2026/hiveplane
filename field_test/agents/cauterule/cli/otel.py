from __future__ import annotations

import click


@click.group("otel")
def otel() -> None:
    """OpenTelemetry export for rule events."""


@otel.command("test")
@click.option("--endpoint", default=None, help="OTLP/HTTP endpoint (overrides config)")
def otel_test(endpoint: str | None) -> None:
    """Send a synthetic span to verify the collector round-trip."""
    from cauterule.config import load_config
    from cauterule.integrations.otel import configure_otlp

    try:
        cfg = load_config().otel
    except (OSError, ValueError) as exc:
        msg = str(exc)
        raise click.ClickException(msg) from exc
    target = endpoint or cfg.endpoint
    exporter = configure_otlp(target, cfg.service_name, dict(cfg.headers))
    exporter.emit_rule_match(
        "R-OTEL-PROBE", agent="otel-test", trigger="synthetic probe", confidence=1.0
    )
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.trace import get_tracer_provider

        provider = get_tracer_provider()
        if isinstance(provider, TracerProvider):
            provider.force_flush(timeout_millis=5000)
    except Exception:
        pass
    click.echo(f"otel test span sent to {target} (service={cfg.service_name})")

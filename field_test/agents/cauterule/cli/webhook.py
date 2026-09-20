from __future__ import annotations

import click


@click.group("webhook")
def webhook() -> None:
    """Promotion webhooks (Slack/Discord/GitHub)."""


@webhook.command("test")
@click.option("--url", default=None, help="Override the configured webhook URL")
def webhook_test(url: str | None) -> None:
    """Send a dry-run promotion payload to the configured URL."""
    from cauterule.config import load_config
    from cauterule.integrations.webhook import build_payload, deliver_payload

    try:
        configured = load_config().webhook
    except (OSError, ValueError) as exc:
        msg = str(exc)
        raise click.ClickException(msg) from exc
    target = url or configured.url
    if not target:
        msg = "no webhook url configured (set [webhook] url or pass --url)"
        raise click.ClickException(msg)
    payload = build_payload(
        configured.provider,
        {"rule_id": "R-DRYRUN", "title": "dry-run probe", "verdict": "promoted"},
    )
    report = deliver_payload(target, payload, configured.max_attempts, configured.backoff)
    click.echo(
        f"dry-run to {configured.provider}: sent={report['sent']} attempts={report['attempts']}"
    )
    if not report["sent"]:
        msg = f"delivery failed (status={report.get('status')})"
        raise click.ClickException(msg)

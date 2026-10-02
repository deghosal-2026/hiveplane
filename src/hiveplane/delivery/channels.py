"""Nine channel adapters that render the envelope per platform (M51-01/02).

Adapters are pure formatters; the actual transport is a ``MessageSender`` so
tests and CI can inject a recording sender. A render failure falls back to
plain text and never drops the event.
"""

from __future__ import annotations

from typing import Protocol

from hiveplane.delivery.models import DeliveryChannel, DeliveryEnvelope


class MessageSender(Protocol):
    """Sends a rendered payload to a channel target."""

    def send(self, channel: DeliveryChannel, target: str, payload: dict[str, object]) -> None: ...


class ChannelAdapter(Protocol):
    """Renders an envelope into a channel-specific payload."""

    channel: DeliveryChannel

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]: ...


def _links(envelope: DeliveryEnvelope) -> list[str]:
    return [
        link
        for link in (envelope.trace_link, envelope.attestation_link, envelope.public_verify_link)
        if link
    ]


def _plain(envelope: DeliveryEnvelope) -> str:
    return envelope.summary or envelope.event_type.value


class SlackAdapter:
    """Slack Block Kit message."""

    channel = DeliveryChannel.SLACK

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        return {
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": _plain(envelope)}},
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": link} for link in _links(envelope)
                    ],
                },
            ],
            "approval_id": envelope.approval_id,
        }


class TeamsAdapter:
    """Teams Adaptive Card."""

    channel = DeliveryChannel.TEAMS

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "body": [{"type": "TextBlock", "text": _plain(envelope)}],
                    },
                }
            ],
        }


class JiraAdapter:
    """Jira issue fields."""

    channel = DeliveryChannel.JIRA

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        return {
            "fields": {
                "summary": _plain(envelope),
                "description": "\n".join(_links(envelope)),
            }
        }


class GitHubPrAdapter:
    """GitHub pull-request comment (markdown)."""

    channel = DeliveryChannel.GITHUB_PR

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        body = _plain(envelope) + ("\n\n" + "\n".join(_links(envelope)) if _links(envelope) else "")
        return {"body": body}


class PagerDutyAdapter:
    """PagerDuty Events API v2 payload."""

    channel = DeliveryChannel.PAGERDUTY

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        return {
            "event_action": "trigger",
            "payload": {
                "summary": _plain(envelope),
                "source": envelope.workload or envelope.run_id,
                "severity": "warning",
            },
            "links": [{"href": link, "text": "trace"} for link in _links(envelope)],
        }


class DiscordAdapter:
    """Discord webhook embed."""

    channel = DeliveryChannel.DISCORD

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        return {"content": _plain(envelope), "embeds": [{"url": link} for link in _links(envelope)]}


class LinearAdapter:
    """Linear issue fields."""

    channel = DeliveryChannel.LINEAR

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        return {"title": _plain(envelope), "description": "\n".join(_links(envelope))}


class EmailAdapter:
    """Email HTML + text body, with a mobile approve link."""

    channel = DeliveryChannel.EMAIL

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        links = "".join(f'<p><a href="{link}">{link}</a></p>' for link in _links(envelope))
        return {
            "subject": f"[HivePlane] {_plain(envelope)}",
            "html": f"<p>{_plain(envelope)}</p>{links}",
            "text": _plain(envelope) + "\n" + "\n".join(_links(envelope)),
            "approval_id": envelope.approval_id,
        }


class WebhookAdapter:
    """Generic structured JSON webhook."""

    channel = DeliveryChannel.WEBHOOK

    def render(self, envelope: DeliveryEnvelope) -> dict[str, object]:
        return envelope.model_dump(mode="json")


CHANNEL_ADAPTERS: dict[DeliveryChannel, ChannelAdapter] = {
    adapter.channel: adapter
    for adapter in (
        SlackAdapter(),
        TeamsAdapter(),
        JiraAdapter(),
        GitHubPrAdapter(),
        PagerDutyAdapter(),
        DiscordAdapter(),
        LinearAdapter(),
        EmailAdapter(),
        WebhookAdapter(),
    )
}


def render(channel: DeliveryChannel, envelope: DeliveryEnvelope) -> dict[str, object]:
    """Render an envelope for a channel, falling back to plain text on error."""
    try:
        return CHANNEL_ADAPTERS[channel].render(envelope)
    except Exception:
        return {"text": _plain(envelope)}

"""Webhook Event Bridge (M8, #161) — CloudEvents-based escalation notifications.

Provides a stateless event bridge that receives escalation events as
CloudEvents, formats them per-platform (Slack Block Kit, Teams Adaptive Card,
generic webhook), and handles HMAC-verified interactive callbacks.

Usage::

    from planner_critic.notifier import Notifier, SlackFormatter, TeamsFormatter

    notifier = Notifier()
    notifier.register("slack", SlackFormatter(webhook_url, signing_secret))
    notifier.register("teams", TeamsFormatter(webhook_url))
    notifier.dispatch(escalation_event)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class EscalationEvent:
    """A CloudEvents-inspired escalation event payload.

    Attributes:
        escalation_id: Unique escalation identifier.
        plan_id: The plan that triggered the escalation.
        reason_code: The reason code for the escalation.
        question: The human-readable escalation question.
        severity: Severity level (blocker, warning, info).
        environment: The execution environment (prod, staging, dev).
        service: The service name.
        summary: A compact plan summary (nodes + edges).
        backstage_url: Optional Backstage review URL.
    """

    escalation_id: str
    plan_id: str
    reason_code: str
    question: str
    severity: str = "blocker"
    environment: str = "unknown"
    service: str = "unknown"
    summary: str = ""
    backstage_url: str | None = None


@dataclass
class NotificationResult:
    """Result of a notification delivery attempt.

    Attributes:
        surface: The surface name (e.g. ``"slack"``, ``"teams"``).
        success: Whether delivery succeeded.
        status_code: HTTP status code if applicable.
        error: Error message if delivery failed.
    """

    surface: str
    success: bool
    status_code: int = 0
    error: str | None = None


class SurfaceFormatter(Protocol):
    """Protocol for platform-specific message formatters."""

    def format_event(self, event: EscalationEvent) -> dict[str, Any]:
        """Format an escalation event into the platform's message format.

        Args:
            event: The escalation event to format.

        Returns:
            A dict representing the formatted message payload.
        """
        ...

    def deliver(self, payload: dict[str, Any]) -> NotificationResult:
        """Deliver the formatted payload to the platform.

        Args:
            payload: The formatted message payload.

        Returns:
            The delivery result.
        """
        ...


class SlackFormatter:
    """Slack Block Kit formatter for escalation events.

    Args:
        webhook_url: Slack Incoming Webhook URL.
        signing_secret: Slack signing secret for HMAC verification.
        channel: Target channel (optional, uses webhook default if None).
    """

    def __init__(
        self,
        webhook_url: str,
        signing_secret: str = "",
        channel: str | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.signing_secret = signing_secret
        self.channel = channel

    def format_event(self, event: EscalationEvent) -> dict[str, Any]:
        color = (
            "#FF0000"
            if event.severity == "blocker"
            else "#FFA500"
            if event.severity == "warning"
            else "#36A64F"
        )
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 Plan Escalation: {event.escalation_id}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:*\n{event.severity}"},
                    {"type": "mrkdwn", "text": f"*Environment:*\n{event.environment}"},
                    {"type": "mrkdwn", "text": f"*Service:*\n{event.service}"},
                    {"type": "mrkdwn", "text": f"*Reason:*\n{event.reason_code}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Question:*\n{event.question}"},
            },
        ]
        if event.summary:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Plan Summary:*\n{event.summary}"},
                }
            )

        actions: list[dict[str, Any]] = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ Approve"},
                "style": "primary",
                "value": f"approve:{event.escalation_id}",
                "action_id": f"approve_{event.escalation_id}",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "❌ Deny"},
                "style": "danger",
                "value": f"deny:{event.escalation_id}",
                "action_id": f"deny_{event.escalation_id}",
            },
        ]
        if event.backstage_url:
            actions.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔍 Review in Backstage"},
                    "url": event.backstage_url,
                    "action_id": f"review_{event.escalation_id}",
                }
            )
        blocks.append({"type": "actions", "elements": actions})

        payload: dict[str, Any] = {"blocks": blocks, "attachments": [{"color": color}]}
        if self.channel:
            payload["channel"] = self.channel
        return payload

    def deliver(self, payload: dict[str, Any]) -> NotificationResult:
        import httpx

        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=10)
            return NotificationResult(
                surface="slack",
                success=resp.is_success,
                status_code=resp.status_code,
                error=None if resp.is_success else resp.text[:200],
            )
        except Exception as err:
            return NotificationResult(surface="slack", success=False, error=str(err))

    def verify_signature(self, timestamp: str, body: str, signature: str) -> bool:
        if not self.signing_secret:
            logger.warning(
                "SlackFormatter: no signing_secret configured — "
                "callbacks accepted without verification!"
            )
            return False
        if abs(time.time() - float(timestamp)) > 300:
            return False
        sig_basestring = f"v0:{timestamp}:{body}"
        expected = (
            "v0="
            + hmac.new(
                self.signing_secret.encode(),
                sig_basestring.encode(),
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)


class TeamsFormatter:
    """MS Teams Adaptive Card v1.4 formatter for escalation events.

    Args:
        webhook_url: Teams Incoming Webhook URL.
    """

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def format_event(self, event: EscalationEvent) -> dict[str, Any]:
        color = (
            "attention"
            if event.severity == "blocker"
            else "warning"
            if event.severity == "warning"
            else "good"
        )
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Large",
                                "weight": "Bolder",
                                "text": f"Plan Escalation: {event.escalation_id}",
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Severity", "value": event.severity},
                                    {"title": "Environment", "value": event.environment},
                                    {"title": "Service", "value": event.service},
                                    {"title": "Reason", "value": event.reason_code},
                                ],
                            },
                            {
                                "type": "TextBlock",
                                "text": f"**Question:** {event.question}",
                                "wrap": True,
                            },
                            *(
                                [
                                    {
                                        "type": "TextBlock",
                                        "text": f"**Summary:** {event.summary}",
                                        "wrap": True,
                                    }
                                ]
                                if event.summary
                                else []
                            ),
                        ],
                        "actions": [
                            {
                                "type": "Action.Http",
                                "title": "✅ Approve",
                                "method": "POST",
                                "url": f"/v1/escalations/{event.escalation_id}/approve",
                            },
                            {
                                "type": "Action.Http",
                                "title": "❌ Deny",
                                "method": "POST",
                                "url": f"/v1/escalations/{event.escalation_id}/deny",
                            },
                        ],
                        "msteams": {"color": color},
                    },
                }
            ],
        }

    def deliver(self, payload: dict[str, Any]) -> NotificationResult:
        import httpx

        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=10)
            return NotificationResult(
                surface="teams",
                success=resp.is_success,
                status_code=resp.status_code,
                error=None if resp.is_success else resp.text[:200],
            )
        except Exception as err:
            return NotificationResult(surface="teams", success=False, error=str(err))


class WebhookFormatter:
    """Generic JSON webhook formatter for escalation events.

    Args:
        webhook_url: The webhook URL.
    """

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def format_event(self, event: EscalationEvent) -> dict[str, Any]:
        return {
            "event": "escalation",
            "escalation_id": event.escalation_id,
            "plan_id": event.plan_id,
            "reason_code": event.reason_code,
            "question": event.question,
            "severity": event.severity,
            "environment": event.environment,
            "service": event.service,
            "summary": event.summary,
            "timestamp": time.time(),
        }

    def deliver(self, payload: dict[str, Any]) -> NotificationResult:
        import httpx

        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=10)
            return NotificationResult(
                surface="webhook",
                success=resp.is_success,
                status_code=resp.status_code,
                error=None if resp.is_success else resp.text[:200],
            )
        except Exception as err:
            return NotificationResult(surface="webhook", success=False, error=str(err))


class Notifier:
    """Multi-surface escalation event notifier.

    Routes escalation events to registered surfaces with at-least-once delivery
    semantics (3 retries, exponential backoff, 5min dedup window).

    Usage::

        notifier = Notifier()
        notifier.register("slack", SlackFormatter(url, secret))
        notifier.register("teams", TeamsFormatter(url))
        results = notifier.dispatch(event)
    """

    DEDUP_TTL: float = 300.0  # 5 minutes

    def __init__(self) -> None:
        self._surfaces: dict[str, SurfaceFormatter] = {}
        self._dedup: dict[str, float] = {}

    def register(self, name: str, formatter: SurfaceFormatter) -> None:
        self._surfaces[name] = formatter

    def dispatch(self, event: EscalationEvent, max_retries: int = 3) -> list[NotificationResult]:
        dedup_key = f"{event.escalation_id}:{event.plan_id}"
        now = time.monotonic()
        # Evict expired entries
        self._dedup = {k: v for k, v in self._dedup.items() if now - v < self.DEDUP_TTL}
        if dedup_key in self._dedup:
            logger.info("notifier: dedup hit for %s — skipping", dedup_key)
            return []
        self._dedup[dedup_key] = now

        results: list[NotificationResult] = []
        for name, formatter in self._surfaces.items():
            for attempt in range(max_retries):
                try:
                    payload = formatter.format_event(event)
                    result = formatter.deliver(payload)
                    results.append(result)
                    if result.success:
                        logger.info(
                            "notifier: %s delivered %s (attempt %d)",
                            name,
                            event.escalation_id,
                            attempt + 1,
                        )
                        break
                    logger.warning(
                        "notifier: %s delivery failed (attempt %d): %s",
                        name,
                        attempt + 1,
                        result.error,
                    )
                    if attempt < max_retries - 1:
                        time.sleep(2**attempt)
                except Exception as err:
                    logger.error("notifier: %s exception (attempt %d): %s", name, attempt + 1, err)
                    if attempt == max_retries - 1:
                        results.append(
                            NotificationResult(surface=name, success=False, error=str(err))
                        )
        return results


__all__ = [
    "EscalationEvent",
    "NotificationResult",
    "Notifier",
    "SlackFormatter",
    "TeamsFormatter",
    "WebhookFormatter",
]

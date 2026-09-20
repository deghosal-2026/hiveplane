"""Webhook notifier — sends POST requests on promotion events (#585).

Sends rule-promotion payloads to Slack / Discord / GitHub / custom
endpoints with retry + backoff, secret redaction, and the SSRF guard
from #508. Configuration lives in ``cauterule.toml [webhook]``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cauterule.export.redaction import redact_export

_logger = logging.getLogger(__name__)

# Hostnames that always resolve to this machine (#508).
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

PROVIDERS = ("slack", "discord", "github", "custom")
RETRYABLE = (429, 500, 502, 503, 504)


def _validate_webhook_url(url: str) -> None:
    """Reject webhook URLs unsafe to POST to (#508).

    Allows ``http``/``https`` only; rejects loopback, link-local (cloud
    metadata, e.g. 169.254.169.254), multicast, and reserved address
    space, plus localhost-style hostnames.

    Raises:
        ValueError: If *url* is not an allowed webhook target.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        raise ValueError(f"invalid webhook URL: {url!r}") from None
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"webhook URL must use http(s): {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host or host in _LOCAL_NAMES:
        raise ValueError(f"webhook URL host not allowed: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # DNS name: resolve best-effort and check the result. Unresolvable
        # names are allowed (offline/air-gapped setups) — the literal-IP
        # checks above are the hard guarantee.
        try:
            resolved = socket.gethostbyname(host)
        except OSError:
            return
        ip = ipaddress.ip_address(resolved)
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise ValueError(f"webhook URL host not allowed: {host!r}")


class WebhookNotifier:
    """Sends HTTP POST notifications when a rule is promoted.

    Args:
        url: Target webhook URL.
        timeout: Request timeout in seconds.
    """

    def __init__(self, url: str, timeout: int = 10) -> None:
        _validate_webhook_url(url)
        self.url = url
        self.timeout = timeout

    def deliver(self, payload: dict[str, Any]) -> bool:
        """POST *payload* once. Returns True on 2xx."""
        report = deliver_payload(self.url, payload, max_attempts=1, timeout=self.timeout)
        return bool(report["sent"])

    def notify_promotion(
        self,
        rule_id: str,
        title: str,
        timestamp: str | None = None,
    ) -> bool:
        """Send a promotion event to the configured webhook URL.

        Args:
            rule_id: Identifier of the promoted rule.
            title: Human-readable title of the rule.
            timestamp: ISO-8601 timestamp; defaults to current UTC time.

        Returns:
            True if the POST succeeded (2xx), False otherwise.
        """
        payload: dict[str, Any] = {
            "event": "rule_promoted",
            "promotion": {
                "rule_id": rule_id,
                "title": title,
                "timestamp": timestamp or datetime.now(UTC).isoformat(),
            },
        }
        return self.deliver(payload)


def build_payload(provider: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Format promotion *fields* for *provider* (slack/discord/github/custom)."""
    if provider not in PROVIDERS:
        raise ValueError(f"webhook provider must be one of {PROVIDERS}, got {provider!r}")
    summary = (
        f"Cauterule promoted {fields.get('rule_id')}: {fields.get('title', '')} "
        f"(verdict={fields.get('verdict', 'promoted')})"
    )
    if provider == "slack":
        return {
            "text": summary,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": summary}}],
        }
    if provider == "discord":
        return {"content": summary}
    if provider == "github":
        return {"event_type": "rule-promoted", "client_payload": fields}
    return {"event": "rule_promoted", "promotion": fields}


def deliver_payload(
    url: str,
    payload: dict[str, Any],
    max_attempts: int = 3,
    backoff: tuple[int, ...] = (1, 5, 30),
    timeout: int = 10,
) -> dict[str, Any]:
    """POST *payload* with retry on 429/5xx. Returns a delivery report."""
    _validate_webhook_url(url)
    body = json.dumps(payload).encode("utf-8")
    attempts = 0
    last_status: int | None = None
    while attempts < max_attempts:
        attempts += 1
        try:
            request = Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urlopen(request, timeout=timeout) as resp:
                last_status = getattr(resp, "status", 200)
                if 200 <= last_status < 300:
                    return {"sent": True, "attempts": attempts, "status": last_status}
                if last_status not in RETRYABLE:
                    return {"sent": False, "attempts": attempts, "status": last_status}
        except HTTPError as exc:
            last_status = exc.code
            if exc.code not in RETRYABLE:
                # Log the host only — never the full URL/query (may embed
                # tokens), per #508.
                host = urlparse(url).hostname or "unknown-host"
                _logger.exception("Webhook POST failed for host %s: status %s", host, exc.code)
                return {"sent": False, "attempts": attempts, "status": exc.code}
        except Exception:
            host = urlparse(url).hostname or "unknown-host"
            _logger.exception("Webhook POST failed for host %s", host)
        if attempts < max_attempts:
            time.sleep(backoff[min(attempts - 1, len(backoff) - 1)])
    return {"sent": False, "attempts": attempts, "status": last_status}


def notify_promotion(
    rule: dict[str, Any],
    store_dir: str = "rules",
    url_override: str | None = None,
) -> dict[str, Any]:
    """Fire the promotion webhook for *rule* when enabled in config.

    Reads ``[webhook]`` from cauterule.toml; returns {"sent": False} with a
    reason when disabled or unconfigured. Delivery is appended to the
    store's webhook delivery log.
    """
    from cauterule.config import load_config

    try:
        webhook = load_config().webhook
    except (OSError, ValueError):
        return {"sent": False, "reason": "config unavailable"}
    if not webhook.enabled:
        return {"sent": False, "reason": "webhook disabled"}
    url = url_override or webhook.url
    if not url:
        raise ValueError("webhook enabled but no url configured (set [webhook] url)")
    fields = {
        "rule_id": rule.get("id"),
        "title": rule.get("title") or rule.get("trigger", ""),
        "trigger_pattern": rule.get("trigger", ""),
        "verdict": rule.get("verdict", "promoted"),
        "evidence_summary": rule.get("evidence_summary", ""),
        "promoted_at": rule.get("promoted_at") or datetime.now(UTC).isoformat(),
        "promoted_by": rule.get("promoted_by", ""),
    }
    if webhook.redact:
        fields = {
            k: (redact_export(str(v)) if isinstance(v, str) else v) for k, v in fields.items()
        }
    payload = build_payload(webhook.provider, fields)
    report = deliver_payload(url, payload, webhook.max_attempts, webhook.backoff)
    try:
        log_path = Path(store_dir) / "webhook-deliveries.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"rule_id": fields["rule_id"], **report}) + "\n")
    except OSError:
        _logger.warning("could not append webhook delivery log")
    return report

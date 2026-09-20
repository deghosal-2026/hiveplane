"""HTTP adapter: POST the invocation payload to a local agent server.

The agent contract mirrors the subprocess adapter: the invocation payload is
sent as JSON in the request body, and the response body is parsed as a
``evalforge.run_envelope.v1`` envelope (with raw-text fallback unless
``strict_output`` is set). A non-200 response is an error.

When sandbox is enabled, the target URL is checked against an optional
allowlist/denylist to enforce network egress policy. Matching is done on
the URL hostname (parsed via ``urllib.parse.urlparse``), not via raw substring
search, to prevent bypasses like ``http://127.0.0.1.evil.com``.

When an egress controller is provided via config, all outbound requests are
checked against the egress policy before being sent. This integrates with the
:class:`evalforge.security.egress.EgressController` for uniform egress control.

Exports:
    HttpAdapter: Adapter that invokes agents over HTTP POST.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from evalforge.adapters.base import Adapter
from evalforge.models.errors import AdapterError, AgentTimeoutError

_SANDBOX_ALLOWED_URLS: set[str] = {"http://localhost", "http://127.0.0.1"}
_SANDBOX_DENIED_URLS: set[str] = set()


def _parse_hostnames(urls: set[str]) -> set[str]:
    """Extract hostnames from a set of URL strings.

    Args:
        urls: A set of URL strings to parse.

    Returns:
        A set of hostnames extracted from the URLs.
    """
    hosts: set[str] = set()
    for u in urls:
        parsed = urlparse(u)
        if parsed.hostname:
            hosts.add(parsed.hostname)
    return hosts


def _check_egress(
    url: str,
    egress_controller: Any,
    method: str = "POST",
    headers: dict[str, str] | None = None,
) -> None:
    """Check an outbound request against the egress policy.

    Args:
        url: The target URL.
        egress_controller: The egress policy controller instance.
        method: HTTP method (default "POST").
        headers: Optional request headers.

    Raises:
        AdapterError: If the egress policy blocks the request.
    """
    allowed, reason = egress_controller.check_request(method, url, headers)
    if not allowed:
        egress_controller.log_request(method, url, "blocked")
        raise AdapterError(f"egress policy blocked request: {reason}")
    egress_controller.log_request(method, url, "allowed")


class HttpAdapter(Adapter):
    """Invoke an agent over HTTP POST.

    Sends the invocation payload as JSON to a configured URL and parses the
    response body as a run envelope. Supports sandbox mode (hostname
    allowlist/denylist) and egress controller integration.

    Attributes:
        name: Identifier "http".
    """

    name = "http"

    def _invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
        """Send the invocation payload as an HTTP POST and return the response text.

        Args:
            payload: The invocation payload dict.
            config: Adapter configuration; requires ``url`` key, optionally
                ``sandbox``, ``sandbox_allowed_urls``, ``sandbox_denied_urls``,
                ``timeout_seconds``, and ``egress_controller``.

        Returns:
            The raw response body as a string.

        Raises:
            AdapterError: If url is missing, sandbox check fails, HTTP request
                fails, or response status is not 200.
            AgentTimeoutError: If the HTTP request times out.
        """
        url = config.get("url")
        if not url:
            raise AdapterError("http adapter requires `url` in config")

        egress_ctrl = config.get("egress_controller")
        if egress_ctrl is not None:
            _check_egress(url, egress_ctrl)

        if config.get("sandbox"):
            allowed = set(config.get("sandbox_allowed_urls", _SANDBOX_ALLOWED_URLS))
            denied = set(config.get("sandbox_denied_urls", _SANDBOX_DENIED_URLS))
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            if hostname == "":
                raise AdapterError("cannot parse hostname for sandbox check")

            denied_hosts = _parse_hostnames(denied)
            if any(
                blocked_host == hostname or hostname.endswith("." + blocked_host)
                for blocked_host in denied_hosts
            ):
                raise AdapterError(
                    f"URL host '{hostname}' is denied in sandbox mode"
                )

            allowed_hosts = _parse_hostnames(allowed)
            if not any(
                hostname == allowed_host or hostname.endswith("." + allowed_host)
                for allowed_host in allowed_hosts
            ):
                raise AdapterError(
                    f"URL host '{hostname}' is not in sandbox allowlist"
                    f" ({allowed_hosts or 'localhost only'})"
                )

        timeout = float(config.get("timeout_seconds", 120))
        try:
            response = httpx.post(url, json=payload, timeout=timeout)
        except httpx.TimeoutException as exc:
            if egress_ctrl is not None:
                egress_ctrl.log_request("POST", url, "timeout")
            raise AgentTimeoutError(f"agent exceeded {timeout}s timeout") from exc
        except httpx.HTTPError as exc:
            if egress_ctrl is not None:
                egress_ctrl.log_request("POST", url, "error")
            raise AdapterError(f"http request failed: {exc}") from exc

        if response.status_code != 200:
            if egress_ctrl is not None:
                egress_ctrl.log_request("POST", url, f"status_{response.status_code}")
            raise AdapterError(
                f"agent returned HTTP {response.status_code}: {response.text[:200]}"
            )
        return response.text

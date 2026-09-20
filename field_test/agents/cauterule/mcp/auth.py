"""Bearer-token authentication for MCP remote mode (#601)."""

from __future__ import annotations

import os


class McpAuthError(Exception):
    """Raised on authentication failure (maps to 401)."""

    status = 401

    def __init__(self, message: str = "unauthorized") -> None:
        """Store the failure message."""
        super().__init__(message)


def resolve_tokens(explicit: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Resolve bearer tokens from explicit config or ``CAUTERULE_MCP_TOKEN``."""
    tokens = [t for t in (explicit or []) if t]
    env_token = os.environ.get("CAUTERULE_MCP_TOKEN", "")
    if env_token:
        tokens.append(env_token)
    return tokens


def bearer_from_headers(headers: dict[str, str]) -> str | None:
    """Extract the bearer token from request headers, if present."""
    for key, value in headers.items():
        if key.lower() == "authorization" and value.lower().startswith("bearer "):
            return value[7:].strip() or None
    return None


def check_bearer(headers: dict[str, str], tokens: list[str], mode: str = "bearer") -> str:
    """Validate the Authorization header. Returns the client identity.

    Raises McpAuthError when authentication fails. ``mode="none"`` skips
    validation (local dev only; caller must gate on loopback host).
    """
    if mode == "none":
        return "anonymous-local"
    if not tokens:
        msg = "server has no tokens configured"
        raise McpAuthError(msg)
    presented = bearer_from_headers(headers)
    if not presented:
        msg = "missing Authorization: Bearer <token>"
        raise McpAuthError(msg)
    for candidate in tokens:
        if presented == candidate:
            return f"token:{presented[:4]}…"
    msg = "invalid token"
    raise McpAuthError(msg)

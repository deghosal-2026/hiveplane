"""Signed operator-UI session cookie (M52-01).

The cookie carries the operator identity and the API key used to reach the
control plane. The signature protects integrity only; authorization is always
the control plane's, using the forwarded key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict

from hiveplane.tenancy.models import Role

SESSION_COOKIE = "hiveplane_session"


class UiSession(BaseModel):
    """The operator identity and API key carried by the session cookie."""

    model_config = ConfigDict(extra="forbid")

    operator_id: str
    tenant_id: str
    role: Role
    api_key: str
    exp: int


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def sign_session(session: UiSession, secret: str) -> str:
    """Serialize and sign a session into a cookie-safe token."""
    raw = session.model_dump_json().encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{payload}.{_signature(payload, secret)}"


def verify_session(token: str, secret: str, *, now: int | None = None) -> UiSession | None:
    """Return the verified session, or ``None`` when invalid or expired."""
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature.encode(), _signature(payload, secret).encode()):
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
        session = UiSession(**data)
    except (ValueError, TypeError):
        return None
    current = int(time.time()) if now is None else now
    if session.exp <= current:
        return None
    return session

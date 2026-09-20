"""Request-scoped context for identity propagation and call metadata.

Uses ``contextvars`` so handlers can access the current client identity
and report backend metadata (URL, HTTP status) without changing their
signature.
"""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class CallMetadata:
    backend_url: str | None = None
    http_status: int | None = None


client_identity: ContextVar[dict | None] = ContextVar("client_identity", default=None)
current_call: ContextVar[CallMetadata] = ContextVar("current_call")

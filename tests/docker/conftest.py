"""Fixtures for the container-layer suite (M23, #93/#94).

The suite runs against **real local inference**. The local LLM is mandatory:
these fixtures FAIL (they never skip) when the OpenAI-compatible endpoint is
unreachable, so a missing model is a red run, not a silent pass.

Configuration is resolved once at import time (before the per-test settings
isolation in ``tests/conftest.py`` scrubs ``HIVEPLANE_*`` env vars), from the
process environment first, then ``.env.local``:

    HIVEPLANE_MODEL__BASE_URL       default ``http://127.0.0.1:8000/v1``
    HIVEPLANE_MODEL__DEFAULT_MODEL  default ``local-model``

The runner (``scripts/docker-test.sh``) exports these so the host-side probe
targets the same endpoint it preflighted. A container-facing
``host.docker.internal`` URL is rewritten to ``127.0.0.1`` because pytest runs on
the host.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

_DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
_DEFAULT_MODEL = "local-model"
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env.local"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _host_base_url(value: str) -> str:
    """Point a container-facing URL back at the host pytest runs on."""
    return value.replace("host.docker.internal", "127.0.0.1")


_FILE_ENV = _read_env_file(_ENV_FILE)
_LOCAL_BASE_URL = _host_base_url(
    os.environ.get("HIVEPLANE_MODEL__BASE_URL")
    or _FILE_ENV.get("HIVEPLANE_MODEL__BASE_URL")
    or _DEFAULT_BASE_URL
).rstrip("/")
_LOCAL_MODEL = (
    os.environ.get("HIVEPLANE_MODEL__DEFAULT_MODEL")
    or _FILE_ENV.get("HIVEPLANE_MODEL__DEFAULT_MODEL")
    or _DEFAULT_MODEL
)


class LocalLLM:
    """A thin client for the OpenAI-compatible local inference endpoint."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url
        self.model = model

    def models(self) -> list[str]:
        """Return the model ids the local server currently serves."""
        with urllib.request.urlopen(f"{self.base_url}/models", timeout=10) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return [str(entry.get("id", "")) for entry in payload.get("data", []) if entry.get("id")]

    def complete(self, prompt: str, *, max_tokens: int = 16) -> dict[str, Any]:
        """Return the raw chat-completion payload for ``prompt``."""
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return payload


@pytest.fixture(scope="session")
def local_llm() -> LocalLLM:
    """Return a working local-LLM client, failing if inference is unavailable.

    The configured model is preferred when the server serves it; otherwise a
    served model is used, because the point of L7 is that real inference works —
    an endpoint with no servable model is a failure, not a skip.
    """
    client = LocalLLM(_LOCAL_BASE_URL, _LOCAL_MODEL)
    try:
        served = client.models()
    except (urllib.error.URLError, OSError, ValueError) as error:
        pytest.fail(
            f"local LLM is required but unreachable at {_LOCAL_BASE_URL}: {error}",
            pytrace=False,
        )
    if not served:
        pytest.fail(
            f"local LLM at {_LOCAL_BASE_URL} serves no models", pytrace=False
        )
    if client.model not in served:
        client.model = sorted(served)[0]
    try:
        payload = client.complete("ping", max_tokens=1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        pytest.fail(
            f"local LLM at {_LOCAL_BASE_URL} (model {client.model!r}) "
            f"failed to serve a completion: {error}",
            pytrace=False,
        )
    if not payload.get("choices"):
        pytest.fail(
            f"local LLM at {_LOCAL_BASE_URL} returned no completion: {payload!r}",
            pytrace=False,
        )
    return client

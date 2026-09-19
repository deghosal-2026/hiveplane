"""Tests for the OpenAI-compatible provider (local + cloud) (M23, #107)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from hiveplane.llm.models import CompletionRequest, Message
from hiveplane.llm.openai import OpenAICompatibleProvider


def _request(model: str = "gpt-4o-2024-08-06") -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="hello")], model=model)


def _openai_response(model: str = "gpt-4o-2024-08-06") -> dict[str, Any]:
    return {
        "model": model,
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_parses_openai_response() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        captured.update(url=url, payload=payload, headers=headers)
        return _openai_response()

    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1", transport=transport
    )

    response = provider.complete(_request())

    assert response.content == "hi"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.finish_reason == "stop"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "hello"}]


def test_cloud_provider_sends_bearer_token() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        captured.update(headers=headers)
        return _openai_response()

    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com/v1", api_key="sk-test", transport=transport
    )
    provider.complete(_request())

    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_local_provider_omits_auth_header() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        captured.update(headers=headers)
        return _openai_response()

    OpenAICompatibleProvider(base_url="http://localhost:11434/v1", transport=transport).complete(
        _request()
    )

    assert "Authorization" not in captured["headers"]


def test_model_identity_is_canonicalized_from_response() -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com/v1",
        model_aliases={"gpt-4o-2024-08-06": "openai/gpt-4o/2024-08-06"},
        transport=lambda url, payload, headers: _openai_response("gpt-4o-2024-08-06"),
    )

    response = provider.complete(_request("gpt-4o-2024-08-06"))

    assert response.model_identity == "openai/gpt-4o/2024-08-06"


def test_default_transport_posts_and_parses() -> None:
    received: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            received["path"] = self.path
            received["body"] = json.loads(self.rfile.read(length))
            payload = json.dumps(_openai_response()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        response = OpenAICompatibleProvider(base_url=base_url).complete(_request())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert received["path"] == "/v1/chat/completions"
    assert response.content == "hi"
    assert response.usage.input_tokens == 10

"""Tiny webhook receiver for the `test` compose profile (M23, #120).

Captures every delivered fan-out payload to stdout and to
``/data/deliveries.jsonl`` so the docker field test (L4) can assert that Slack
and generic webhook deliveries actually arrived. Standard library only, so it
runs on a bare ``python:3.12-slim`` image with no extra dependencies.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_DELIVERIES = Path(os.environ.get("WEBHOOK_SINK_DATA", "/data")) / "deliveries.jsonl"
_PORT = int(os.environ.get("WEBHOOK_SINK_PORT", "8081"))


def _announce(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


class _Handler(BaseHTTPRequestHandler):
    def _record(self, path: str, body: str) -> None:
        _DELIVERIES.parent.mkdir(parents=True, exist_ok=True)
        with _DELIVERIES.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"path": path, "body": body}) + "\n")
        _announce(json.dumps({"path": path, "body": body}))

    def _reply(self, status: int, payload: dict[str, str]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self._reply(200, {"status": "ok"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        self._record(self.path, body)
        self._reply(200, {"status": "received"})

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    """Serve the webhook sink until interrupted."""
    server = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)
    _announce(f"webhook-sink listening on :{_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()

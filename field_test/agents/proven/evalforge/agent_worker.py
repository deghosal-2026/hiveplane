#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
from typing import Any

import uuid


def _setup_env() -> None:
    """Point ChatOpenAI at the local OMLX server before any agent import."""
    # Ensure a writable HOME/TMPDIR for agents that write under ~ or /tmp
    try:
        agent_dir = os.environ.get("EVALFORGE_FIELD_AGENT_DIR") or os.getcwd()
        cache_dir = os.path.join(agent_dir, ".cache")
        tmp_dir = os.path.join(cache_dir, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        os.environ.setdefault("HOME", cache_dir)
        os.environ.setdefault("TMPDIR", tmp_dir)
        os.environ.setdefault("XDG_CACHE_HOME", cache_dir)
    except Exception:
        # Best-effort; don't block if directories can't be created
        pass
    os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
    os.environ.setdefault("OPENAI_BASE_URL", os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"))
    model = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-9B-MLX-4bit")
    try:
        from langchain_openai import ChatOpenAI as _CO
        _orig = _CO.__init__
        def _patched(self, *a, **kw):
            if "model" not in kw and "model_name" not in kw:
                kw["model"] = model
            return _orig(self, *a, **kw)
        _CO.__init__ = _patched
    except Exception:
        pass

    # Install a broad HTTP capture for LLM calls (OpenAI/Ollama/Anthropic/etc.).
    # Always-on in field harness. Writes under $EVALFORGE_FIELD_OUTPUT_DIR/llm.
    try:
        _install_llm_capture()
    except Exception:
        # Capture is best-effort; never block agent execution if it fails.
        pass


def _load_payload() -> dict[str, Any]:
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        raise RuntimeError("stdin payload must be a JSON object")
    return data


def _call_builder(builder: Any, payload: dict[str, Any], model: str | None) -> Any:
    kwargs = {"model": model} if model else {}
    sig = inspect.signature(builder)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect._empty
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if not required:
        return builder(**kwargs)
    return builder(payload, **kwargs)


def _langgraph(payload: dict[str, Any], module: str, function: str, model: str | None) -> dict[str, Any]:
    mod = importlib.import_module(module)
    builder = getattr(mod, function)
    if hasattr(builder, "invoke"):
        agent = builder
    else:
        agent = _call_builder(builder, payload, model)
    _tid = str(uuid.uuid4())
    result = agent.invoke(
        {"messages": [{"role": "user", "content": payload.get("input", "")}], "task_id": _tid},
        {"configurable": {"thread_id": _tid, "task_id": _tid}},
    )
    messages = result.get("messages", []) if isinstance(result, dict) else []
    final = None
    steps: list[dict[str, Any]] = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                steps.append({"type": "tool_call", "tool": getattr(tc, "name", ""), "args": getattr(tc, "args", {}), "duration_ms": None})
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            final = content
    if final:
        steps.append({"type": "response", "content": final, "duration_ms": None})
    return {
        "schema_version": "evalforge.run_envelope.v1",
        "status": "completed",
        "output": {"final": final, "structured": None},
        "trajectory": {"steps": steps},
        "cost": None,
        "error": None,
    }


def _pydantic_ai(payload: dict[str, Any], module: str, function: str, model: str | None) -> dict[str, Any]:
    mod = importlib.import_module(module)
    builder = getattr(mod, function)
    if hasattr(builder, "invoke"):
        agent = builder
    else:
        agent = _call_builder(builder, payload, model)
    result = agent.run_sync(payload.get("input", ""))
    final = None
    steps: list[dict[str, Any]] = []
    for msg in result.all_messages() if hasattr(result, "all_messages") else []:
        for part in getattr(msg, "parts", []):
            kind_str = str(getattr(part, "part_kind", None) or getattr(part, "kind", "") or "")
            if callable(kind_str):
                try:
                    kind_str = str(kind_str())
                except Exception:
                    kind_str = ""
            if kind_str in ("tool-call", "tool_call"):
                steps.append({"type": "tool_call", "tool": getattr(part, "tool_name", ""), "args": getattr(part, "args", {}), "duration_ms": None})
            elif kind_str in ("tool-return", "tool_return"):
                steps.append({"type": "tool_result", "tool": getattr(part, "tool_name", ""), "result": getattr(part, "content", None), "duration_ms": None})
            elif kind_str in ("final", "return", "text"):
                final = getattr(part, "content", "") or final
    structured = getattr(result, "data", None)
    if structured is not None:
        final = final or str(structured)
    if final:
        steps.append({"type": "response", "content": final, "duration_ms": None})
    return {
        "schema_version": "evalforge.run_envelope.v1",
        "status": "completed",
        "output": {"final": final, "structured": structured if isinstance(structured, dict) else None},
        "trajectory": {"steps": steps},
        "cost": None,
        "error": None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter-type", required=True, choices=["langgraph", "pydantic-ai"])
    ap.add_argument("--module", required=True)
    ap.add_argument("--function", default="build_agent")
    ap.add_argument("--model")
    args = ap.parse_args()
    _setup_env()
    payload = _load_payload()
    try:
        if args.adapter_type == "langgraph":
            envelope = _langgraph(payload, args.module, args.function, args.model)
        else:
            envelope = _pydantic_ai(payload, args.module, args.function, args.model)
    except Exception as exc:
        print(json.dumps({
            "schema_version": "evalforge.run_envelope.v1",
            "status": "error",
            "output": {"final": None, "structured": None},
            "trajectory": {"steps": []},
            "cost": None,
            "error": str(exc),
        }))
        return 0
    print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
def _install_llm_capture() -> None:
    """Monkeypatch httpx.Client.request to persist LLM request+response pairs.

    Heuristics to detect LLM calls:
    - URL paths containing common endpoints: '/chat/completions', '/v1/messages', '/api/chat'.
    - Content-Type application/json.

    Artifacts are written to: "$EVALFORGE_FIELD_OUTPUT_DIR/llm/NNNN-{provider}-request.json" and
    matching "...-response.json". Provider is inferred from the URL host.
    """
    import threading
    import time
    from pathlib import Path
    try:
        import httpx  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"httpx not available for LLM capture: {exc}")

    out_dir = os.environ.get("EVALFORGE_FIELD_OUTPUT_DIR")
    if not out_dir:
        return
    base = Path(out_dir) / "llm"
    base.mkdir(parents=True, exist_ok=True)

    # Lazy import sanitizer
    def _sanitize(text: str) -> str:
        try:
            from evalforge.security.sanitize import sanitize_output  # type: ignore
            return sanitize_output(text)
        except Exception:
            return text

    counter = {"n": 0}
    lock = threading.Lock()

    _orig_request = httpx.Client.request

    def _looks_like_llm(url: str, headers: dict[str, str] | None) -> bool:
        path = url.lower()
        if any(p in path for p in ("/chat/completions", "/v1/messages", "/api/chat")):
            return True
        # Fallback: inspect content-type header when available
        ct = (headers or {}).get("content-type") or (headers or {}).get("Content-Type")
        return bool(ct and "application/json" in ct.lower())

    def _provider_hint(url: str) -> str:
        try:
            from urllib.parse import urlparse
            netloc = urlparse(url).netloc
            if "openai" in netloc:
                return "openai"
            if "anthropic" in netloc:
                return "anthropic"
            if "openrouter" in netloc:
                return "openrouter"
            if "ollama" in netloc:
                return "ollama"
            if "127.0.0.1" in netloc or "localhost" in netloc:
                return "local"
            return netloc.split(":")[0]
        except Exception:
            return "unknown"

    def _origin(url: str) -> str:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            return "local" if host in {"127.0.0.1", "localhost"} else "cloud"
        except Exception:
            return "unknown"

    def _next_id() -> str:
        with lock:
            counter["n"] += 1
            return f"{counter['n']:04d}"

    def _jsonify(obj: Any) -> str:
        try:
            import json as _json
            return _json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception:
            return str(obj)

    def _request(self, method: str, url: str, *args: Any, **kwargs: Any):  # type: ignore[override]
        should_capture = _looks_like_llm(url, kwargs.get("headers"))
        rid = _next_id() if should_capture else ""
        provider = _provider_hint(url) if should_capture else ""
        req_path = base / f"{rid}-{provider}-request.json" if should_capture else None
        resp_path = base / f"{rid}-{provider}-response.json" if should_capture else None

        req_meta = {}
        if should_capture and req_path is not None:
            body = kwargs.get("content") or kwargs.get("data") or kwargs.get("json")
            req_model = None
            if isinstance(body, dict):
                req_model = body.get("model")
            try:
                req_payload = {
                    "ts": time.time(),
                    "method": method,
                    "url": url,
                    "headers": kwargs.get("headers") or {},
                    "json": body if not isinstance(body, (bytes, bytearray)) else None,
                    "meta": {
                        "provider": provider,
                        "origin": _origin(url),
                        "model": req_model,
                    },
                }
                req_meta = req_payload.get("meta", {})
            except Exception:
                req_payload = {"ts": time.time(), "method": method, "url": url}
            try:
                req_path.write_text(_sanitize(_jsonify(req_payload)))
            except Exception:
                pass

        resp = _orig_request(self, method, url, *args, **kwargs)

        if should_capture and resp_path is not None:
            try:
                content_type = resp.headers.get("content-type", "")
                raw = resp.text if "json" in content_type.lower() else "<non-json response>"
                # Try to parse model name from response JSON (OpenAI-compatible)
                resp_model = None
                if raw and raw != "<non-json response>":
                    try:
                        import json as _json
                        parsed = _json.loads(raw)
                        if isinstance(parsed, dict):
                            resp_model = parsed.get("model") or parsed.get("id")
                    except Exception:
                        pass
                resp_payload = {
                    "ts": time.time(),
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "body": raw,
                    "meta": {
                        "provider": provider,
                        "origin": _origin(url),
                        "model": resp_model or req_meta.get("model"),
                        "endpoint": url,
                        "request_id": resp.headers.get("x-request-id") or resp.headers.get("x-openai-request-id"),
                    },
                }
                resp_path.write_text(_sanitize(_jsonify(resp_payload)))
            except Exception:
                pass
        return resp

    # Install once
    if getattr(httpx.Client.request, "_evalforge_patched", False):  # type: ignore[attr-defined]
        return
    httpx.Client.request = _request  # type: ignore[assignment]
    setattr(httpx.Client.request, "_evalforge_patched", True)  # type: ignore[attr-defined]

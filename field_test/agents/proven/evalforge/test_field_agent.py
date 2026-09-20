"""Field test: run scenarios against a cloned agent with full scoring."""
import json
import os
from pathlib import Path

import pytest

from evalforge.adapters.factory import create_adapter
from evalforge.loading.pack_loader import load_pack
from evalforge.scoring.engine import ScoringEngine
from evalforge.scoring.judge.openai import OpenAIClient
from evalforge.scoring.judge.ollama import OllamaClient
from evalforge.scoring.judge.mlx import MLXJudgeClient


def _install_http_capture():
    """Capture HTTP request/response pairs for judge calls in field tests.

    Writes to $EVALFORGE_FIELD_OUTPUT_DIR/llm in the current test process so
    judge requests are always persisted (agent LLM calls are captured in the
    worker process via field/agent_worker.py).
    """
    import os
    from pathlib import Path
    try:
        import httpx  # type: ignore
    except Exception:
        return

    if getattr(httpx.Client.request, "_evalforge_field_patched", False):  # type: ignore[attr-defined]
        return

    out_dir = os.environ.get("EVALFORGE_FIELD_OUTPUT_DIR")
    if not out_dir:
        return
    base = Path(out_dir) / "llm"
    base.mkdir(parents=True, exist_ok=True)

    def _sanitize(txt: str) -> str:
        try:
            from evalforge.security.sanitize import sanitize_output  # type: ignore
            return sanitize_output(txt)
        except Exception:
            return txt

    import json as _json
    from urllib.parse import urlparse
    counter = {"n": 0}

    def _next_id():
        counter["n"] += 1
        return f"J{counter['n']:04d}"

    _orig = httpx.Client.request

    def _req(self, method, url, *a, **kw):  # type: ignore[override]
        rid = _next_id()
        netloc = urlparse(url).netloc
        provider = (
            "openai" if "openai" in netloc else
            ("anthropic" if "anthropic" in netloc else ("openrouter" if "openrouter" in netloc else ("local" if any(h in netloc for h in ("127.0.0.1","localhost")) else netloc.split(":")[0])))
        )
        # Request
        try:
            body = kw.get("json") if "json" in kw else (kw.get("content") or kw.get("data"))
            req = {
                "method": method,
                "url": url,
                "headers": kw.get("headers") or {},
                "json": body if not isinstance(body, (bytes, bytearray)) else None,
                "meta": {
                    "provider": provider,
                    "origin": "local" if any(h in netloc for h in ("127.0.0.1","localhost")) else "cloud",
                    "model": (body or {}).get("model") if isinstance(body, dict) else None,
                }
            }
            (base / f"{rid}-{provider}-request.json").write_text(_sanitize(_json.dumps(req, indent=2)))
        except Exception:
            pass

        resp = _orig(self, method, url, *a, **kw)
        try:
            ctype = resp.headers.get("content-type", "").lower()
            raw = resp.text if "json" in ctype else "<non-json response>"
            model = None
            if raw and raw != "<non-json response>":
                try:
                    parsed = _json.loads(raw)
                    if isinstance(parsed, dict):
                        model = parsed.get("model") or parsed.get("id")
                except Exception:
                    pass
            out = {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": raw,
                "meta": {
                    "provider": provider,
                    "origin": "local" if any(h in netloc for h in ("127.0.0.1","localhost")) else "cloud",
                    "model": model,
                    "endpoint": url,
                    "request_id": resp.headers.get("x-request-id") or resp.headers.get("x-openai-request-id"),
                }
            }
            (base / f"{rid}-{provider}-response.json").write_text(_sanitize(_json.dumps(out, indent=2)))
        except Exception:
            pass
        return resp

    httpx.Client.request = _req  # type: ignore[assignment]
    setattr(httpx.Client.request, "_evalforge_field_patched", True)  # type: ignore[attr-defined]

def _load_field_config(slug: str) -> tuple[dict, bool]:
    config_dir = os.environ.get("EVALFORGE_FIELD_CONFIG_DIR")
    if not config_dir:
        return {}, True
    path = Path(config_dir) / f"{slug}.json"
    if not path.exists():
        return {}, True
    data = json.loads(path.read_text())
    adapter_cfg = data.get("adapter_config", {}) or {}
    has_entry = bool(adapter_cfg)
    return {
        "type": data.get("adapter_type", "python"),
        **adapter_cfg,
    }, has_entry


@pytest.mark.field
def test_agent_scenario(scenario, agent_config, monkeypatch):
    slug = os.environ.get("EVALFORGE_FIELD_AGENT", "unknown")
    field_cfg, has_entry_point = _load_field_config(slug)

    if os.environ.get("EVALFORGE_FIELD_CONFIG_DIR") and not has_entry_point:
        pytest.skip(f"{slug}: no entry point configured in field/config")

    cfg = {**agent_config, **field_cfg, "run_id": f"field-{scenario.id}"}

    # 0. Ensure judge HTTP capture is active in this test process
    _install_http_capture()

    # 1. Run the agent
    artifact = create_adapter(cfg).run(scenario, cfg)

    # 2. Save artifact/output for inspection
    output_dir = os.environ.get("EVALFORGE_FIELD_OUTPUT_DIR", "")
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "artifact.json").write_text(artifact.model_dump_json(indent=2))
        (out / "prompt.txt").write_text(scenario.input or "")
        (out / "completion.txt").write_text(
            artifact.output.final or artifact.error or ""
        )

    # 3. Run scoring engine against the scenario pack
    pack_path = os.environ.get("EVALFORGE_FIELD_PACK")
    assert pack_path, "EVALFORGE_FIELD_PACK must be set"
    pack = load_pack(pack_path)

    engine = ScoringEngine(pack)
    judge = _make_field_judge()
    run_score = engine.score_run([artifact], judge=judge)

    # 4. Save scores and judge evidence (always when present)
    if output_dir:
        out = Path(output_dir)
        for sid, ss in run_score.scenario_scores.items():
            # Full metric dump including details
            metrics_payload = {
                m: {
                    "score": s.score,
                    "threshold": s.threshold,
                    "passed": s.passed,
                    "category": s.category,
                    "source": s.source,
                    "error": s.error,
                    "detail": s.detail,
                } for m, s in ss.metric_results.items()
            }
            (out / f"score-{sid}.json").write_text(json.dumps({
                "status": ss.status,
                "metrics": metrics_payload,
                "violations": ss.safety_violations,
            }, indent=2))

            # Persist judge prompt + verdict for any judge-scored metrics
            judge_dir = out / "judge" / sid
            wrote_any = False
            for m, s in ss.metric_results.items():
                if s.source == "judge":
                    detail = s.detail or {}
                    prompt = str(detail.get("prompt", ""))
                    judge_info = detail.get("judge", {}) or {}
                    verdict = {
                        "metric": m,
                        "score": s.score,
                        "rationale": detail.get("rationale", ""),
                        "judge": judge_info,
                    }
                    (judge_dir).mkdir(parents=True, exist_ok=True)
                    (judge_dir / f"{m}-prompt.txt").write_text(prompt)
                    (judge_dir / f"{m}-verdict.json").write_text(json.dumps(verdict, indent=2))
                    wrote_any = True
            # Mark directory if none written (helps triage)
            if wrote_any and not (judge_dir / ".ok").exists():
                (judge_dir / ".ok").write_text("")

    # 5. Assert agent ran and scoring passed
    assert artifact.status == "completed", (
        f"Agent failed scenario '{scenario.id}': {artifact.status}"
        + (f" — {artifact.error}" if artifact.error else "")
    )
    ss = run_score.scenario_scores.get(scenario.id)
    assert ss is not None, f"no score for scenario {scenario.id}"

    # 5a. Hard guard: if any metric used a judge, ensure provider is not 'mock' and evidence exists
    judge_metrics = [
        (m, s)
        for m, s in ss.metric_results.items()
        if getattr(s, "source", "") == "judge"
    ]
    if output_dir and judge_metrics:
        out = Path(output_dir)
        # Verify llm capture exists
        llm_dir = out / "llm"
        assert llm_dir.exists() and any(llm_dir.glob("*-request.json")), (
            f"judge present for {scenario.id} but no LLM HTTP capture in {llm_dir}"
        )
        # Verify each judge metric has provider != mock and evidence files exist
        for m, s in judge_metrics:
            detail = s.detail or {}
            judge_info = detail.get("judge", {}) if isinstance(detail, dict) else {}
            if not isinstance(judge_info, dict):
                judge_info = {}
            provider = judge_info.get("provider")
            assert provider and provider != "mock", (
                f"judge provider invalid for {scenario.id}/{m}: {provider!r}"
            )
            jdir = out / "judge" / scenario.id
            assert (jdir / f"{m}-prompt.txt").exists(), f"missing judge prompt for {scenario.id}/{m}"
            assert (jdir / f"{m}-verdict.json").exists(), f"missing judge verdict for {scenario.id}/{m}"
    assert ss.status == "passed", (
        f"Scenario '{scenario.id}' scored '{ss.status}' — "
        + ", ".join(f"{m}={s.passed}" for m, s in ss.metric_results.items())
    )
def _make_field_judge():
    """Construct a real judge client from field env vars.

    Rules:
    - Default to OpenAI-compatible API at EVALFORGE_FIELD_ENDPOINT with model
      EVALFORGE_FIELD_MODEL. This covers both local OMLX gateways and cloud
      proxies (e.g., OpenRouter).
    - If EVALFORGE_FIELD_JUDGE_PROVIDER is set to 'ollama' or 'mlx', use
      the corresponding local clients.
    - API key resolution (OpenAI-compatible):
        OPENAI_API_KEY → OPENROUTER_API_KEY → 'omlx-test' (local tier only)
    """
    import os

    provider = os.environ.get("EVALFORGE_FIELD_JUDGE_PROVIDER", "").strip()
    endpoint = os.environ.get("EVALFORGE_FIELD_JUDGE_ENDPOINT") or os.environ.get("EVALFORGE_FIELD_ENDPOINT")
    model = os.environ.get("EVALFORGE_FIELD_JUDGE_MODEL") or os.environ.get("EVALFORGE_FIELD_MODEL") or "gpt-4o-mini"
    tier = os.environ.get("EVALFORGE_FIELD_TIER", "local").lower()

    if provider.lower() == "ollama":
        base_url = endpoint or "http://localhost:11434"
        return OllamaClient(model=model, base_url=base_url)
    if provider.lower() == "mlx":
        # Prefer provided endpoint; otherwise, manage an mlx-lm server locally
        if endpoint:
            api_key = os.environ.get("OPENAI_API_KEY") or "omlx-test"
            return OpenAIClient(api_key=api_key, model=model, base_url=endpoint)
        return MLXJudgeClient(model=model)

    # Default: OpenAI-compatible client to the configured endpoint (local OMLX or cloud)
    api_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or ("omlx-test" if tier == "local" else None)
    )
    base_url = endpoint or "https://api.openai.com/v1"
    if api_key is None:
        raise RuntimeError("No API key set for judge (OPENAI_API_KEY/OPENROUTER_API_KEY)")
    return OpenAIClient(api_key=api_key, model=model, base_url=base_url)

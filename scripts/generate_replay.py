"""Generate the deterministic LLM replay fixture for the example corpora (M23, #137).

Drives every example agent through its real corpus tasks with a capturing
context (tool data served from ``deploy/testdata/tools``, requests recorded
instead of completed) and writes ``deploy/testdata/llm/replay.json`` keyed by
``hiveplane.llm.fake.replay_key``. The recorded completion for each task is the
corpus task's expected model output, so CI certification is deterministic
without a live model. See docs/design/corpus-fixture-coupling.md (D20).

Usage: ``.venv/bin/python scripts/generate_replay.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hiveplane.certification.corpus import load_corpus  # noqa: E402
from hiveplane.certification.models import BenchmarkTask, CheckType  # noqa: E402
from hiveplane.core.manifest import load_manifest  # noqa: E402
from hiveplane.core.spec import canonical_model_identity  # noqa: E402
from hiveplane.llm.fake import replay_key  # noqa: E402
from hiveplane.llm.models import CompletionRequest, Message  # noqa: E402
from hiveplane.shaping.injection import InjectionScanner  # noqa: E402
from hiveplane.shaping.pipeline import ShapingPipeline  # noqa: E402

WORKLOADS = ("repo-agent", "docs-agent", "incident-agent")
CORPORA_DIR = ROOT / "examples" / "corpora"
WORKLOADS_DIR = ROOT / "examples" / "workloads"
TOOLS_DIR = ROOT / "deploy" / "testdata" / "tools"
OUT_FILE = ROOT / "deploy" / "testdata" / "llm" / "replay.json"

#: Model output for action-audit tasks, where the check constrains tool calls
#: rather than content. repo-agent's contract flags high-risk PRs, so its
#: replay output must classify trap tasks as high (M23 D20 workload contract).
_ACTION_PAYLOADS: dict[str, dict[str, str]] = {
    "repo-agent": {"risk": "high", "summary": "flagged as high risk"},
    "docs-agent": {"draft": "drafted for review"},
    "incident-agent": {"severity": "warning", "summary": "triaged alert"},
}


class CaptureContext:
    """Routes tool calls to fixtures and records completion requests."""

    def __init__(self, model: str, shaping: ShapingPipeline | None, spec: Any) -> None:
        self.model = model
        self._shaping = shaping
        self._spec = spec
        self.requests: list[CompletionRequest] = []

    def tool_call(self, tool_id: str, **_kwargs: Any) -> Any:
        raw = (TOOLS_DIR / f"{tool_id}.json").read_text(encoding="utf-8")
        if self._shaping is not None and self._spec is not None:
            shaped = self._shaping.apply(raw, self._spec)
            text = shaped.text
        else:
            text = raw
        return SimpleNamespace(shaped_output=SimpleNamespace(text=text))

    def complete(self, prompt: str | list[Message], **_kwargs: Any) -> Any:
        messages = (
            [Message(role="user", content=prompt)] if isinstance(prompt, str) else list(prompt)
        )
        request = CompletionRequest(messages=messages, model=self.model)
        self.requests.append(request)
        return SimpleNamespace(content=json.dumps({"captured": True}))

    def report_usage(self, **_kwargs: Any) -> None:
        """Usage is reported by the real seam; the capture harness ignores it."""

    def checkpoint(self) -> None:
        """Cooperative checkpoints are no-ops while capturing."""


def expected_completion(workload: str, task: BenchmarkTask) -> str:
    """Return the replayed completion that satisfies ``task``'s check."""
    if task.check.type is CheckType.EXACT_MATCH and task.check.value is not None:
        payload: dict[str, Any] = {task.check.field: task.check.value}
        if workload in ("repo-agent", "incident-agent"):
            payload.setdefault("summary", task.name)
        elif workload == "docs-agent" and task.check.field != "draft":
            payload.setdefault("draft", task.name)
        return json.dumps(payload)
    return json.dumps(dict(_ACTION_PAYLOADS[workload]))


def drive_task(
    workload: str, entrypoint: str, task: BenchmarkTask, ctx: CaptureContext
) -> str:
    """Run the real agent for ``task`` and return the captured request key."""
    if workload == "docs-agent":
        import examples.docs_agent as docs_agent
        from langgraph.types import Command

        config: dict[str, Any] = {
            "configurable": {"thread_id": f"gen-{task.id}", "hiveplane_ctx": ctx}
        }
        list(docs_agent.graph.stream({"task": dict(task.input)}, config, stream_mode="values"))
        list(docs_agent.graph.stream(Command(resume=True), config, stream_mode="values"))
    elif workload == "repo-agent":
        import examples.repo_agent as repo_agent

        repo_agent.run(dict(task.input), ctx)  # type: ignore[arg-type]
    else:
        import examples.incident_agent as incident_agent

        incident_agent.run(dict(task.input), ctx)  # type: ignore[arg-type]
    if not ctx.requests:
        raise RuntimeError(f"{workload} task {task.id} produced no model call")
    return replay_key(ctx.requests[-1])


def generate_replay() -> dict[str, str]:
    """Return the full replay map for every example workload's corpus."""
    replay: dict[str, str] = {}
    shaping = ShapingPipeline(InjectionScanner())
    for workload in WORKLOADS:
        manifest = load_manifest(WORKLOADS_DIR / f"{workload}.yaml")
        identity = manifest.spec.model.identity
        if identity is None:
            raise RuntimeError(f"{workload} manifest has no model identity")
        model = canonical_model_identity(identity)
        corpus = load_corpus(CORPORA_DIR / workload / "v1")
        for task in corpus.tasks:
            ctx = CaptureContext(model, shaping, manifest.spec.output_shaping)
            key = drive_task(workload, manifest.spec.runtime.entrypoint, task, ctx)
            if key in replay and replay[key] != expected_completion(workload, task):
                raise RuntimeError(f"conflicting replay entries for key {key!r}")
            replay[key] = expected_completion(workload, task)
    return replay


def main() -> int:
    """Regenerate ``deploy/testdata/llm/replay.json``."""
    replay = generate_replay()
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps(replay, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    sys.stdout.write(
        f"wrote {len(replay)} replay entries to {OUT_FILE.relative_to(ROOT)}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

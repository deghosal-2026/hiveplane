"""The `ask` copilot as a first-class certified workload (M53-03, dogfooding)."""

from __future__ import annotations

from hiveplane.certification.models import (
    BenchmarkCorpus,
    BenchmarkTask,
    BenchmarkTaskCheck,
    CheckType,
)
from hiveplane.core.manifest import parse_manifest
from hiveplane.core.workload import AgentWorkload

ASK_WORKLOAD_NAME = "ask"
ASK_CORPUS_ID = "corpora/ask/v1"

_FIXED_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("why did run 42 fail?", "run_failure"),
    ("show team platform spend last week", "team_spend"),
    ("who approved approval ap-1?", "approval_attribution"),
    ("what is the health of summarizer?", "workload_health"),
    ("what is running right now?", "fleet_status"),
)


def ask_manifest() -> AgentWorkload:
    """Return the certified `ask` workload manifest (dogfooding the thesis)."""
    return parse_manifest(
        {
            "apiVersion": "hiveplane/v1",
            "kind": "AgentWorkload",
            "metadata": {
                "name": ASK_WORKLOAD_NAME,
                "owner": "hiveplane",
                "team": "platform",
            },
            "spec": {
                "runtime": {
                    "adapter": "raw-worker",
                    "entrypoint": "hiveplane.ask.entrypoint:run",
                },
                "budget": {"per_run_usd": 0.1, "per_day_usd": 10.0, "per_team_usd": 100.0},
                "model": {
                    "strategy": "tiered",
                    "identity": {
                        "provider": "openai",
                        "family": "gpt-4o",
                        "version": "2024-08-06",
                    },
                },
                "certification": {
                    "benchmark_corpus": ASK_CORPUS_ID,
                    "staging_threshold": 0.8,
                    "production_threshold": 0.9,
                    "status": "uncertified",
                },
            },
        }
    )


def ask_corpus() -> BenchmarkCorpus:
    """Return the fixed question corpus `ask` must keep passing."""
    tasks = [
        BenchmarkTask(
            id=f"ask-{index:02d}",
            name=question,
            input={"question": question},
            check=BenchmarkTaskCheck(
                type=CheckType.EXACT_MATCH, field="intent", value=intent
            ),
            critical=True,
        )
        for index, (question, intent) in enumerate(_FIXED_QUESTIONS, start=1)
    ]
    return BenchmarkCorpus(id=ASK_CORPUS_ID, version=1, tasks=tasks)

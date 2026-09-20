from __future__ import annotations

from pathlib import Path

import click


@click.command("init")
@click.option("--dir", "target_dir", default=".", help="Target directory for scaffolding.")
@click.option(
    "--adapter",
    "adapter",
    type=click.Choice(["none", "langgraph", "crewai", "pydanticai", "custom"]),
    default="none",
    help="Framework adapter to scaffold an example for.",
)
def init(target_dir: str, adapter: str) -> None:
    """Scaffold cauterule.toml, rules/, .gitignore, example agent, and first rule."""
    d = Path(target_dir)
    d.mkdir(parents=True, exist_ok=True)
    toml_path = d / "cauterule.toml"
    if not toml_path.exists():
        toml_text = (
            '[llm]\nprovider = "openai"\nmodel = "gpt-4o"\n'
            '\n[paths]\nrules = "rules"\ntrajectories = "trajectories"\n'
        )
        toml_path.write_text(toml_text, encoding="utf-8")
        click.echo(f"Created {toml_path}")
    rules_dir = d / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    traj_dir = d / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    _update_gitignore(d / ".gitignore")
    click.echo(f"Scaffolded CauterRule project in {d.resolve()}")

    if adapter != "none":
        _scaffold_example(d, adapter)


def _update_gitignore(gitignore_path: Path) -> None:
    """Create *gitignore_path* or append only the missing cauterule entries."""
    entries = ["*.pyc", "__pycache__/", ".venv/"]
    if not gitignore_path.exists():
        gitignore_path.write_text("\n".join(entries) + "\n", encoding="utf-8")
        return
    existing = gitignore_path.read_text(encoding="utf-8")
    existing_lines = set(existing.splitlines())
    missing = [e for e in entries if e not in existing_lines]
    if not missing:
        return
    separator = "" if not existing or existing.endswith("\n") else "\n"
    gitignore_path.write_text(
        existing + separator + "\n".join(missing) + "\n", encoding="utf-8"
    )


def _scaffold_example(d: Path, adapter: str) -> None:
    """Write the framework-specific example agent + a starter rule (#534/#536/#537)."""
    from cauterule.models.rule import Provenance, RuleDo, RuleWhen, StandingRule
    from cauterule.serialization.rule_yaml import dump_rule_to_file

    # Starter rule shared by all adapters.
    rule = StandingRule(
        id="R-001",
        when=RuleWhen(trigger="deploy fails with connection timeout"),
        do=RuleDo(directive="check service health and retry with backoff"),
        confidence=0.85,
        provenance=Provenance(
            source_trajectory="scaffold",
            extracted_by="init",
            extract_timestamp="2026-01-01T00:00:00Z",
            extraction_pass=1,
        ),
        status="active",
        promoted_at="2026-01-01T00:00:00Z",
    )
    dump_rule_to_file(rule, d / "rules" / "R-001.yaml")

    examples = {
        "langgraph": _LANGGRAPH_EXAMPLE,
        "crewai": _CREWAI_EXAMPLE,
        "pydanticai": _PYDANTICAI_EXAMPLE,
        "custom": _CUSTOM_EXAMPLE,
    }
    filename = d / f"agent_{adapter}_example.py"
    filename.write_text(examples[adapter], encoding="utf-8")
    click.echo(f"Created {filename}")


_LANGGRAPH_EXAMPLE = '''"""LangGraph adapter example (#534)."""
from cauterule.adapter.langgraph import capture_node_error, inject_rules


def my_node(state: dict) -> dict:
    """A LangGraph node: inject rules, run, capture failures."""
    state = inject_rules(state, task="sync billing records")
    try:
        # ... your node logic here ...
        state["billing"] = "synced"
        return state
    except Exception as exc:
        capture_node_error("my_node", state, state, exc)
        raise
'''


_CREWAI_EXAMPLE = '''"""CrewAI adapter example (#536)."""
from cauterule.adapter.crewai import CrewaiTracer, inject_crew_rules


def build_crew():
    from crewai import Agent, Crew, Task

    tracer = CrewaiTracer(task="reconcile invoices", base_dir="trajectories")
    description = (
        "Reconcile outstanding invoices for the accounting period.\\n"
        + inject_crew_rules("reconcile invoices")
    )
    return Crew(
        agents=[Agent(role="accountant", goal="reconcile", backstory="ex")],
        tasks=[Task(description=description, expected_output="ledger")],
        tasks_handlers=[tracer.on_task_complete, tracer.on_tool_error],
    )
'''


_PYDANTICAI_EXAMPLE = '''"""PydanticAI adapter example (#537)."""
from cauterule.adapter.pydanticai import inject_system_rules, watch_run


@watch_run(task="answer billing question", base_dir="trajectories")
async def run_agent(prompt: str):
    # ... your pydantic_ai Agent.run(...) here ...
    return "answer"

# Option B: explicit system-prompt injection
async def run_with_rules(prompt: str):
    rules_text = inject_system_rules("answer billing question")
    # result = await agent.run(prompt, system_prompt=(base_system + rules_text))
    return rules_text
'''


_CUSTOM_EXAMPLE = '''"""Custom-loop adapter example (#538)."""
import asyncio

from cauterule.adapter import ainject, inject, watch


@watch(base_dir="trajectories", redact_keys={"api_key", "token"})
def sync_agent(prompt: str) -> str:
    return "done"


@watch(base_dir="trajectories")
async def async_agent(prompt: str) -> str:
    return "done"


def sync_loop(task: str) -> None:
    with inject(task, rules=None) as matched:
        print(f"Injected {len(matched)} rules")


async def async_loop(task: str) -> None:
    async with ainject(task) as matched:
        print(f"Injected {len(matched)} rules")
'''

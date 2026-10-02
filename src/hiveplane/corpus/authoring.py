"""Corpus authoring helpers: scaffold, add, validate, lint (M55-01)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import JsonValue

from hiveplane.certification.corpus import CORPUS_FILENAME, load_corpus, parse_corpus
from hiveplane.certification.errors import CorpusError
from hiveplane.certification.models import (
    BenchmarkCorpus,
    BenchmarkTask,
    BenchmarkTaskCheck,
    CheckType,
)
from hiveplane.corpus.templates import TemplateKind, corpus_template


def corpus_document(corpus: BenchmarkCorpus) -> str:
    """Render a corpus as YAML text."""
    return yaml.safe_dump(
        corpus.model_dump(mode="json", by_alias=True),
        sort_keys=False,
        allow_unicode=True,
    )


def write_corpus(path: str | Path, corpus: BenchmarkCorpus) -> Path:
    """Write a corpus to ``path`` (a directory gets ``corpus.yaml``)."""
    target = Path(path)
    if target.is_dir():
        target = target / CORPUS_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(corpus_document(corpus))
    return target


def scaffold_corpus(
    directory: str | Path,
    *,
    corpus_id: str,
    template: TemplateKind = TemplateKind.REPO_AGENT,
    version: int = 1,
    force: bool = False,
) -> Path:
    """Write a starter corpus for a workload type; refuse to clobber unless forced."""
    target = Path(directory) / CORPUS_FILENAME
    if target.exists() and not force:
        raise CorpusError(f"corpus already exists: {target} (use --force to overwrite)")
    corpus = corpus_template(template, corpus_id=corpus_id, version=version)
    return write_corpus(target, corpus)


def validate_corpus(path: str | Path) -> BenchmarkCorpus:
    """Load and validate a corpus, raising :class:`CorpusError` with details."""
    return load_corpus(path)


def lint_corpus(corpus: BenchmarkCorpus) -> list[str]:
    """Return actionable warnings about a corpus that is otherwise valid."""
    warnings: list[str] = []
    if not any(task.critical for task in corpus.tasks):
        warnings.append("no task is marked critical; critical failures cannot gate")
    fast = [task for task in corpus.tasks if "fast" in task.profiles]
    if not fast:
        warnings.append("no task is in the 'fast' profile; the dev loop cannot run")
    if all("fast" in task.profiles for task in corpus.tasks):
        warnings.append("every task is in the 'fast' profile; fast equals full")
    if len({task.id for task in corpus.tasks}) != len(corpus.tasks):
        warnings.append("task ids must be unique")
    return warnings


def add_task(
    path: str | Path,
    *,
    task_id: str,
    name: str,
    field: str,
    value: str,
    input_data: dict[str, JsonValue] | None = None,
    critical: bool = True,
    fast: bool = False,
) -> BenchmarkCorpus:
    """Append a task to a corpus on disk and rewrite it (immutably revalidated)."""
    corpus = load_corpus(path)
    task = BenchmarkTask(
        id=task_id,
        name=name,
        input=input_data or {},
        check=BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field=field, value=value),
        critical=critical,
        profiles=["fast", "full"] if fast else ["full"],
    )
    updated = corpus.model_copy(update={"tasks": [*corpus.tasks, task]})
    # Re-validate the whole corpus so a duplicate id or bad task is rejected.
    updated = parse_corpus(updated.model_dump(mode="json", by_alias=True))
    write_corpus(path, updated)
    return updated

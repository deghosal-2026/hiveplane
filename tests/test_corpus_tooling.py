"""Tests for corpus authoring, templates, and profiles (M55-01/02/04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hiveplane.certification.errors import CorpusError
from hiveplane.certification.models import TargetContext
from hiveplane.corpus.authoring import (
    add_task,
    lint_corpus,
    scaffold_corpus,
    validate_corpus,
)
from hiveplane.corpus.profiles import (
    EmptyProfileError,
    Profile,
    ProfileNotAllowedError,
    ensure_profile_allowed,
    select_profile,
)
from hiveplane.corpus.templates import TemplateKind, corpus_template

_KINDS = list(TemplateKind)


@pytest.mark.parametrize("kind", _KINDS)
def test_each_template_is_valid_and_has_a_fast_subset(kind: TemplateKind) -> None:
    corpus = corpus_template(kind, corpus_id=f"{kind.value}-corpus")

    assert corpus.tasks
    fast = select_profile(corpus, Profile.FAST)
    assert 0 < len(fast.tasks) < len(corpus.tasks)
    assert select_profile(corpus, Profile.FULL).tasks == corpus.tasks


def test_select_fast_requires_fast_tasks() -> None:
    corpus = corpus_template(TemplateKind.TRIAGE, corpus_id="triage")
    full_only = corpus.model_copy(
        update={
            "tasks": [
                task.model_copy(update={"profiles": ["full"]}) for task in corpus.tasks
            ]
        }
    )

    with pytest.raises(EmptyProfileError):
        select_profile(full_only, Profile.FAST)


def test_fast_profile_is_refused_for_production() -> None:
    with pytest.raises(ProfileNotAllowedError):
        ensure_profile_allowed(Profile.FAST, TargetContext.PRODUCTION)


def test_full_profile_is_allowed_for_production() -> None:
    ensure_profile_allowed(Profile.FULL, TargetContext.PRODUCTION)


def test_scaffold_writes_a_valid_corpus(tmp_path: Path) -> None:
    path = scaffold_corpus(
        tmp_path / "corpora" / "demo",
        corpus_id="demo-corpus",
        template=TemplateKind.REPO_AGENT,
    )

    assert path.name == "corpus.yaml"
    corpus = validate_corpus(path)
    assert corpus.id == "demo-corpus"
    assert corpus.version == 1


def test_scaffold_refuses_to_clobber_without_force(tmp_path: Path) -> None:
    scaffold_corpus(tmp_path, corpus_id="demo")

    with pytest.raises(CorpusError):
        scaffold_corpus(tmp_path, corpus_id="demo")

    scaffold_corpus(tmp_path, corpus_id="demo", force=True)


def test_add_task_appends_and_persists(tmp_path: Path) -> None:
    path = scaffold_corpus(tmp_path, corpus_id="demo", template=TemplateKind.TRIAGE)
    before = validate_corpus(path)

    updated = add_task(
        path,
        task_id="extra",
        name="extra case",
        field="output",
        value="ok",
        critical=False,
    )

    assert len(updated.tasks) == len(before.tasks) + 1
    assert validate_corpus(path).tasks[-1].id == "extra"


def test_add_task_rejects_duplicate_id(tmp_path: Path) -> None:
    path = scaffold_corpus(tmp_path, corpus_id="demo", template=TemplateKind.TRIAGE)
    existing = validate_corpus(path).tasks[0].id

    with pytest.raises(CorpusError):
        add_task(path, task_id=existing, name="dup", field="output", value="x")


def test_validate_reports_actionable_error(tmp_path: Path) -> None:
    bad = tmp_path / "corpus.yaml"
    bad.write_text("id: broken\ntasks: []\n")

    with pytest.raises(CorpusError) as exc:
        validate_corpus(bad)

    assert "invalid" in str(exc.value).lower() or "task" in str(exc.value).lower()


def test_lint_flags_missing_critical_and_fast(tmp_path: Path) -> None:
    corpus = corpus_template(TemplateKind.GENERATION, corpus_id="gen")
    stripped = corpus.model_copy(
        update={
            "tasks": [
                task.model_copy(update={"profiles": ["full"], "critical": False})
                for task in corpus.tasks
            ]
        }
    )

    warnings = lint_corpus(stripped)

    assert any("critical" in warning for warning in warnings)
    assert any("fast" in warning for warning in warnings)

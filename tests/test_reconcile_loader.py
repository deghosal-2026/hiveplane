"""Unit tests for the desired-state loader (M26-01)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from hiveplane.fleet.reconcile import SpecKind, SpecSource
from hiveplane.reconcile.loader import (
    DesiredStateLoader,
    GitSource,
    LoaderError,
    authenticated_url,
)

_WORKLOAD = """\
kind: workload
metadata:
  name: agent-1
  team: platform
spec:
  runtime: {adapter: raw-worker, entrypoint: "examples.worker:run"}
  model:
    strategy: tiered
    identity: {provider: openai, family: gpt-4o, version: "2024-08-06"}
  certification:
    benchmark_corpus: corpora/agent-1/v1
    staging_threshold: 0.8
    production_threshold: 0.9
    status: uncertified
  budget: {per_run_usd: 0.5, per_day_usd: 5.0, per_team_usd: 50.0}
"""

_POLICY = """\
kind: policy_pack
metadata: {name: platform-baseline, team: platform, version: "3"}
spec:
  defaults: {sandbox_for_destructive: true, injection_scan: true}
"""

_TRIGGER = """\
kind: trigger
metadata: {name: pr-analysis, workload: agent-1}
spec:
  source: github
  target_kind: workload
  target_ref: agent-1
  admission_rule: auto
"""

_BUDGET = """\
kind: budget
metadata: {name: platform-budget, team: platform}
spec:
  periods: {day: 50, week: 250}
  hard_stop: true
"""


def _write(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content)
    return path


def test_load_directory_parses_workload(tmp_path: Path) -> None:
    _write(tmp_path, "agent.yaml", _WORKLOAD)
    desired = DesiredStateLoader().load_directory(tmp_path, source_id="git-main")
    spec = desired.get(SpecKind.WORKLOAD, "agent-1")
    assert spec is not None
    assert spec.source is SpecSource.GIT
    assert spec.kind is SpecKind.WORKLOAD
    assert cast(dict[str, Any], spec.spec)["metadata"]["team"] == "platform"


def test_load_directory_reads_multiple_documents_and_kinds(tmp_path: Path) -> None:
    _write(tmp_path, "all.yaml", _WORKLOAD + "---\n" + _POLICY + "---\n" + _TRIGGER)
    _write(tmp_path, "budget.yaml", _BUDGET)
    desired = DesiredStateLoader().load_directory(tmp_path, source_id="git-main")
    assert desired.names(SpecKind.WORKLOAD) == ["agent-1"]
    assert desired.names(SpecKind.POLICY) == ["platform-baseline"]
    assert desired.names(SpecKind.TRIGGER) == ["pr-analysis"]
    assert desired.names(SpecKind.BUDGET) == ["platform-budget"]


def test_content_hash_is_deterministic_across_formatting(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", _WORKLOAD)
    first = DesiredStateLoader().load_directory(tmp_path, source_id="git-main")
    # Re-serialize the workload with different key order/whitespace.
    reordered = """\
spec:
  budget: {per_day_usd: 5.0, per_run_usd: 0.5, per_team_usd: 50.0}
  certification: {
    status: uncertified,
    production_threshold: 0.9,
    staging_threshold: 0.8,
    benchmark_corpus: corpora/agent-1/v1,
  }
  model: {identity: {version: "2024-08-06", family: gpt-4o, provider: openai}, strategy: tiered}
  runtime: {entrypoint: "examples.worker:run", adapter: raw-worker}
kind: workload
metadata: {team: platform, name: agent-1}
"""
    other = tmp_path / "b"
    other.mkdir()
    _write(other, "a.yaml", reordered)
    second = DesiredStateLoader().load_directory(other, source_id="git-main")
    first_spec = first.get(SpecKind.WORKLOAD, "agent-1")
    second_spec = second.get(SpecKind.WORKLOAD, "agent-1")
    assert first_spec is not None and second_spec is not None
    assert first_spec.content_hash == second_spec.content_hash


def test_content_hash_changes_with_spec(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", _WORKLOAD)
    first = DesiredStateLoader().load_directory(tmp_path, source_id="git-main")
    other = tmp_path / "b"
    other.mkdir()
    _write(other, "a.yaml", _WORKLOAD.replace("0.9", "0.95"))
    second = DesiredStateLoader().load_directory(other, source_id="git-main")
    first_spec = first.get(SpecKind.WORKLOAD, "agent-1")
    second_spec = second.get(SpecKind.WORKLOAD, "agent-1")
    assert first_spec is not None and second_spec is not None
    assert first_spec.content_hash != second_spec.content_hash


def test_invalid_document_fails_the_whole_revision(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", _WORKLOAD)
    _write(tmp_path, "bad.yaml", "kind: workload\nmetadata: {name: broken}\nspec: {}\n")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="git-main")


def test_unknown_kind_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", "kind: spaceship\nmetadata: {name: x}\nspec: {}\n")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="git-main")


def test_duplicate_object_in_set_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", _WORKLOAD)
    _write(tmp_path, "b.yaml", _WORKLOAD)
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_directory(tmp_path, source_id="git-main")


def test_revision_defaults_to_directory_digest(tmp_path: Path) -> None:
    _write(tmp_path, "a.yaml", _WORKLOAD)
    desired = DesiredStateLoader().load_directory(tmp_path, source_id="git-main")
    assert desired.revision


def test_authenticated_url_injects_token() -> None:
    source = GitSource(
        url="https://github.com/acme/fleet.git", auth_secret_ref="git/token"
    )
    assert (
        authenticated_url(source, "s3cret")
        == "https://x-access-token:s3cret@github.com/acme/fleet.git"
    )


def _git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    _write(path, "agent.yaml", _WORKLOAD)
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return _git(path, "rev-parse", "HEAD")


def test_load_git_resolves_revision_and_is_read_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit = _init_repo(repo)
    loader = DesiredStateLoader()
    source = GitSource(url=str(repo), path=".", ref=commit)
    desired = loader.load_git(source, source_id="git-main")
    assert desired.revision == commit
    assert desired.names(SpecKind.WORKLOAD) == ["agent-1"]
    # The source repo is untouched: same HEAD, clean tree.
    assert _git(repo, "rev-parse", "HEAD") == commit
    assert _git(repo, "status", "--porcelain") == ""


def test_load_git_rejects_missing_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    source = GitSource(url=str(repo), path=".", ref="does-not-exist")
    with pytest.raises(LoaderError):
        DesiredStateLoader().load_git(source, source_id="git-main")

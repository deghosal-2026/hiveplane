from __future__ import annotations
from typing import TypedDict, NotRequired, Annotated
from enum import Enum


def _append_errors(a: list[str], b: list[str]) -> list[str]:
    return a + b


class CommitType(Enum):
    feat = "feat"
    fix = "fix"
    refactor = "refactor"
    docs = "docs"
    chore = "chore"
    test = "test"
    perf = "perf"
    style = "style"
    build = "build"
    ci = "ci"
    revert = "revert"
    unknown = "unknown"


class ConventionalCommit(TypedDict):
    sha: str
    raw_message: str
    type: CommitType
    scope: str | None
    description: str
    body: str
    is_breaking: bool
    breaking_footer: str | None
    author: str
    date: str
    pr_number: int | None


class ClassifiedCommit(TypedDict):
    commit: ConventionalCommit
    is_notable: bool
    confidence: float
    rationale: str


class BreakingChange(TypedDict):
    commit_sha: str
    scope: str | None
    footer_text: str
    proposed_migration: str
    severity: str
    disclosure: str
    human_edited: bool
    human_migration_text: NotRequired[str]


class SecurityFix(TypedDict):
    commit_sha: str
    cve_ids: list[str]
    cvss_score: float | None
    affected_versions: str
    advisory_draft: str
    disclosure: str
    human_edited: bool
    human_advisory_text: NotRequired[str]


class SemverProposal(TypedDict):
    current_version: str
    proposed_version: str
    bump_type: str
    breaking_count: int
    feature_count: int
    fix_count: int
    justification: str


class RoutingPlan(TypedDict):
    commit_sha: str
    sub_agent: str
    priority: int


class ToneExample(TypedDict):
    source: str
    text: str


class AgentState(TypedDict):
    repo: str
    base_tag: str
    head_tag: str
    mode: str
    github_token: NotRequired[str | None]
    no_cache: NotRequired[bool]
    commits: NotRequired[list[ConventionalCommit]]
    releases: NotRequired[list[dict]]
    readme_content: NotRequired[str]
    repo_tag_convention: NotRequired[str]
    classified_commits: NotRequired[list[ClassifiedCommit]]
    breaking_changes: NotRequired[list[BreakingChange]]
    security_fixes: NotRequired[list[SecurityFix]]
    pending_routing: NotRequired[list[RoutingPlan]]
    current_interrupt_index: NotRequired[int]
    breaking_approvals: NotRequired[list[bool]]
    security_approvals: NotRequired[list[bool]]
    tone_examples: NotRequired[list[ToneExample]]
    draft_notes: NotRequired[str]
    semver_proposal: NotRequired[SemverProposal | None]
    semver_override: NotRequired[str | None]
    semver_cancelled: NotRequired[bool]
    semver_approved: NotRequired[bool]
    errors: Annotated[list[str], _append_errors]
    token_usage: NotRequired[dict[str, int]]
    cache_hits: NotRequired[int]

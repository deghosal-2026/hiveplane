"""Security corpus types and data models for M5 (Security & Trust Oracle).

A *security instance* is a normalized record from a known CVE fix, carrying
enough context for the planner-critic loop to run against a Goal derived from
the issue description, and enough ground truth to score the critic's findings.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CWEBucket(StrEnum):
    """The six CWE buckets targeted by the v0.2.0 security oracle."""

    XSS = "XSS"
    SQL_INJECTION = "SQLI"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    AUTHENTICATION = "AUTH"
    DESERIALIZATION = "DESERIALIZATION"
    SSRF = "SSRF"
    SECRET_HANDLING = "SECRETS"  # noqa: S105  # CWE bucket label, not a credential


CWE_LABELS: dict[CWEBucket, set[str]] = {
    CWEBucket.XSS: {"CWE-79"},
    CWEBucket.SQL_INJECTION: {"CWE-89"},
    CWEBucket.PATH_TRAVERSAL: {"CWE-22"},
    CWEBucket.AUTHENTICATION: {"CWE-287", "CWE-862"},
    CWEBucket.DESERIALIZATION: {"CWE-502"},
    CWEBucket.SSRF: {"CWE-918"},
    CWEBucket.SECRET_HANDLING: {"CWE-798", "CWE-200", "CWE-312"},
}


class ExpectedCriticSignal(StrEnum):
    """Which heuristic family the critic should fire on for this instance."""

    MISSING_STEPS = "missing_steps"
    UNSAFE_SEQUENCING = "unsafe_sequencing"
    UNVERIFIED_DEPENDENCIES = "unverified_dependencies"
    WEAK_ROLLBACK = "weak_rollback"
    RISK = "risk"
    FEASIBILITY = "feasibility"
    MULTIPLE = "multiple"


class SecurityInstance(BaseModel):
    """A normalized, pinned security fix instance for critic oracle evaluation.

    Attributes:
        instance_id: Unique identifier (e.g. ``CWE-079-001``).
        repo_owner: Repository owner (e.g. ``django``).
        repo_name: Repository name.
        commit_hash: The fix commit SHA.
        patch_sha: Checksum of the patch diff.
        cwe: Primary CWE identifier (e.g. ``CWE-79``).
        cwe_bucket: One of the six CWE buckets above.
        vulnerability_class: High-level class (e.g. ``cross_site_scripting``).
        license: License of the repo (MIT, Apache-2.0, BSD-3-Clause).
        issue_description: Narrative description of the vulnerability / task.
        goal_text: Goal text used to create a Goal for the planner.
        ground_truth_summary: Human-readable summary of the correct fix.
        expected_critic_signal: Which heuristic should fire.
        expected_reason_codes: Specific reason codes the critic should emit (optional).
        checksum: SHA-256 of the full instance payload for pinning.
        provenance: Free-form dict with any extra provenance data.
    """

    instance_id: str
    repo_owner: str = "synthetic"
    repo_name: str = "synthetic"
    commit_hash: str = "synthetic"
    patch_sha: str = "synthetic"
    cwe: str
    cwe_bucket: CWEBucket
    vulnerability_class: str
    license: str = "MIT"
    issue_description: str
    goal_text: str
    ground_truth_summary: str
    expected_critic_signal: ExpectedCriticSignal | None = None
    expected_reason_codes: list[str] = Field(default_factory=list)
    ground_truth_patch: str = ""
    checksum: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)


class CorpusManifest(BaseModel):
    """A checksum-signed manifest for a corpus directory.

    Attributes:
        name: Corpus name (e.g. ``swebench-security``).
        version: Semantic version for the corpus snapshot.
        created: Date the manifest was generated.
        instance_count: Number of instances.
        instances: Mapping of instance_id → SHA-256 checksum.
        cwe_counts: Mapping of CWE bucket → count.
        description: Human-readable description.
    """

    name: str
    version: str = "0.1.0"
    created: date
    instance_count: int
    instances: dict[str, str] = Field(default_factory=dict)
    cwe_counts: dict[CWEBucket, int] = Field(default_factory=dict)
    description: str = ""


__all__ = [
    "CWE_LABELS",
    "CWEBucket",
    "CorpusManifest",
    "ExpectedCriticSignal",
    "SecurityInstance",
]

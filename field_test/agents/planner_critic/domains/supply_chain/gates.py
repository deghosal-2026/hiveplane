"""Supply-chain domain gates (M4, #141).

Three domain-specific deterministic gates:
- TransitiveLockingGate — manifest edit without lockfile regeneration
- BreakingChangeGate — major semver bump without migration/linter
- ArtifactIntegrityGate — deploy of unsigned/unattested artifact
"""

from __future__ import annotations

from planner_critic.gates.base import BaseGate
from planner_critic.reason_codes import (
    SUPPLY_CHAIN_BREAKING_CHANGE_WITHOUT_MIGRATION,
    SUPPLY_CHAIN_LOCKFILE_NOT_REGENERATED,
    SUPPLY_CHAIN_MISSING_SBOM,
    SUPPLY_CHAIN_UNSIGNED_ARTIFACT,
)
from planner_critic.schema.plan import PlanVersion
from planner_critic.types import Finding, Severity

MANIFEST_ACTIONS = frozenset({"edit_manifest", "edit_dependency", "add_dependency"})
LOCKFILE_ACTIONS = frozenset({"regenerate_lockfile", "update_lockfile", "install"})
LOCKFILE_TARGETS = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "go.sum",
        "composer.lock",
    }
)


class TransitiveLockingGate(BaseGate):
    """Flags manifest edits without a prior lockfile regeneration."""

    name = "supply_chain_transitive_locking"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_regen = any(
            t.action in LOCKFILE_ACTIONS or any(tg in (t.target or "") for tg in LOCKFILE_TARGETS)
            for t in plan.tasks
        )
        for task in plan.tasks:
            if task.action in MANIFEST_ACTIONS and not has_regen:
                findings.append(
                    Finding(
                        id=(f"supply_chain_transitive_locking:{plan.id}:{plan.version}:{task.id}"),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=SUPPLY_CHAIN_LOCKFILE_NOT_REGENERATED,
                        message=(
                            f"manifest edit {task.id!r} has no prior lockfile regeneration step"
                        ),
                        suggested_fix=("Add a regenerate_lockfile step after the manifest edit"),
                    )
                )
        return findings


class BreakingChangeGate(BaseGate):
    """Flags major semver bumps without a migration script or linter."""

    name = "supply_chain_breaking_change"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_migration = any(
            t.action in ("run_migration", "write_migration", "migrate") for t in plan.tasks
        )
        has_linter = any(
            t.action in ("run_linter", "lint") or t.action.endswith("_lint") for t in plan.tasks
        )
        for task in plan.tasks:
            if task.action in ("bump_major", "breaking_change") and not (
                has_migration and has_linter
            ):
                findings.append(
                    Finding(
                        id=(f"supply_chain_breaking_change:{plan.id}:{plan.version}:{task.id}"),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=SUPPLY_CHAIN_BREAKING_CHANGE_WITHOUT_MIGRATION,
                        message=(
                            f"major version bump {task.id!r} lacks a "
                            f"migration script or linter check"
                        ),
                        suggested_fix=(
                            "Add run_migration and run_linter steps before the major bump deploy"
                        ),
                    )
                )
        return findings


class ArtifactIntegrityGate(BaseGate):
    """Flags deploys of unsigned or unattested artifacts."""

    name = "supply_chain_artifact_integrity"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_sign = any(t.action in ("sign_artifact", "sign", "cosign") for t in plan.tasks)
        has_sbom = any(t.action in ("generate_sbom", "generate_bom", "sbom") for t in plan.tasks)
        for task in plan.tasks:
            if task.action not in ("deploy", "release", "publish"):
                continue
            if not has_sign:
                findings.append(
                    Finding(
                        id=(
                            f"supply_chain_artifact_integrity:"
                            f"{plan.id}:{plan.version}:{task.id}:sign"
                        ),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=SUPPLY_CHAIN_UNSIGNED_ARTIFACT,
                        message=(f"deploy step {task.id!r} references an unsigned artifact"),
                        suggested_fix=("Add a sign_artifact step before deploy"),
                    )
                )
            if not has_sbom:
                findings.append(
                    Finding(
                        id=(
                            f"supply_chain_artifact_integrity:"
                            f"{plan.id}:{plan.version}:{task.id}:sbom"
                        ),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=SUPPLY_CHAIN_MISSING_SBOM,
                        message=(f"deploy step {task.id!r} has no generated SBOM"),
                        suggested_fix=("Add a generate_sbom step before deploy"),
                    )
                )
        return findings


SUPPLY_CHAIN_PRECONDITIONS = {
    "lockfile_regenerated": "The lockfile has been regenerated",
    "migration_script_passed": "The migration script passed",
    "artifact_signed": "The artifact has been signed",
    "sbom_generated": "An SBOM has been generated",
    "linter_clean": "The linter is clean",
}

SUPPLY_CHAIN_CRITIC_PROMPT = (
    "Audit this plan from a software supply-chain security perspective. "
    "Pay attention to: (1) manifest edits without lockfile regeneration, "
    "(2) major version bumps without migration scripts or linters, "
    "(3) deploys of unsigned artifacts or artifacts without an SBOM.\n"
)


__all__ = [
    "SUPPLY_CHAIN_CRITIC_PROMPT",
    "SUPPLY_CHAIN_PRECONDITIONS",
    "ArtifactIntegrityGate",
    "BreakingChangeGate",
    "TransitiveLockingGate",
]

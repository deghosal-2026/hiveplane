"""Data engineering domain gates (M4, #143).

Three domain-specific deterministic gates:
- SchemaPreVerificationGate — destructive query without verified backup
- SLAWindowGate — migration outside maintenance window
- DualWriteGate — live migration without dual-write/fallback
"""

from __future__ import annotations

from planner_critic.gates.base import BaseGate
from planner_critic.reason_codes import (
    DATA_ENG_DESTRUCTIVE_WITHOUT_BACKUP,
    DATA_ENG_MIGRATION_OUTSIDE_MAINTENANCE_WINDOW,
    DATA_ENG_MIGRATION_WITHOUT_DUAL_WRITE,
    DATA_ENG_MIGRATION_WITHOUT_FALLBACK,
)
from planner_critic.schema.plan import PlanVersion
from planner_critic.types import Finding, Severity

DESTRUCTIVE_QUERIES = frozenset({"drop_table", "truncate", "delete_rows", "drop_db"})
BACKUP_ACTIONS = frozenset({"create_backup", "backup"})
VERIFY_BACKUP_ACTIONS = frozenset({"verify_restorable", "verify_backup"})
WINDOW_ACTIONS = frozenset({"maintenance_window", "open_window", "begin_window"})
MIGRATE_ACTIONS = frozenset({"migrate", "live_migrate", "apply_migration"})
DUAL_WRITE_ACTIONS = frozenset({"enable_dual_write", "dual_write"})
FALLBACK_ACTIONS = frozenset({"define_fallback", "fallback_path"})


class SchemaPreVerificationGate(BaseGate):
    """Flags destructive queries without a verified-restorable backup."""

    name = "data_eng_schema_pre_verification"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_backup = any(t.action in BACKUP_ACTIONS for t in plan.tasks)
        has_verified = any(t.action in VERIFY_BACKUP_ACTIONS for t in plan.tasks)
        for task in plan.tasks:
            if task.action in DESTRUCTIVE_QUERIES and not (has_backup and has_verified):
                findings.append(
                    Finding(
                        id=(f"data_eng_schema_pre_verification:{plan.id}:{plan.version}:{task.id}"),
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=DATA_ENG_DESTRUCTIVE_WITHOUT_BACKUP,
                        message=(
                            f"destructive query {task.id!r} lacks a "
                            f"verified-restorable backup "
                            f"(backup={has_backup}, verified={has_verified})"
                        ),
                        suggested_fix=(
                            "Add create_backup and verify_restorable steps "
                            "before the destructive query"
                        ),
                    )
                )
        return findings


class SLAWindowGate(BaseGate):
    """Flags migrations that run outside an active maintenance window."""

    name = "data_eng_sla_window"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        in_window = any(t.action in WINDOW_ACTIONS for t in plan.tasks)
        for task in plan.tasks:
            if task.action in MIGRATE_ACTIONS and not in_window:
                findings.append(
                    Finding(
                        id=f"data_eng_sla_window:{plan.id}:{plan.version}:{task.id}",
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=DATA_ENG_MIGRATION_OUTSIDE_MAINTENANCE_WINDOW,
                        message=(
                            f"migration step {task.id!r} runs outside an active maintenance window"
                        ),
                        suggested_fix=("Add a maintenance_window step before the migration"),
                    )
                )
        return findings


class DualWriteGate(BaseGate):
    """Flags live migrations without dual-write or a fallback path."""

    name = "data_eng_dual_write"

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        has_dual = any(t.action in DUAL_WRITE_ACTIONS for t in plan.tasks)
        has_fallback = any(t.action in FALLBACK_ACTIONS for t in plan.tasks)
        for task in plan.tasks:
            if task.action not in ("live_migrate", "migrate"):
                continue
            if not has_dual:
                findings.append(
                    Finding(
                        id=f"data_eng_dual_write:{plan.id}:{plan.version}:{task.id}:dual",
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=DATA_ENG_MIGRATION_WITHOUT_DUAL_WRITE,
                        message=(f"live migration {task.id!r} has no dual-write enabled"),
                        suggested_fix="Add an enable_dual_write step",
                    )
                )
            if not has_fallback:
                findings.append(
                    Finding(
                        id=f"data_eng_dual_write:{plan.id}:{plan.version}:{task.id}:fb",
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=DATA_ENG_MIGRATION_WITHOUT_FALLBACK,
                        message=(f"live migration {task.id!r} has no fallback path"),
                        suggested_fix="Add a define_fallback step",
                    )
                )
        return findings


DATA_ENG_PRECONDITIONS = {
    "backup_created": "A backup of the target exists",
    "backup_verified_restorable": "The backup has been verified as restorable",
    "maintenance_window_active": "The maintenance window is active",
    "dual_write_enabled": "Dual-write is enabled",
    "fallback_path_defined": "A fallback path is defined",
    "schema_compatibility_checked": "Schema compatibility has been checked",
}

DATA_ENG_CRITIC_PROMPT = (
    "Audit this plan from a data-engineering / database-reliability "
    "perspective. Pay attention to: (1) destructive queries without a "
    "verified-restorable backup, (2) migrations outside the maintenance "
    "window, (3) live migrations without dual-write or a fallback path.\n"
)


__all__ = [
    "DATA_ENG_CRITIC_PROMPT",
    "DATA_ENG_PRECONDITIONS",
    "DualWriteGate",
    "SLAWindowGate",
    "SchemaPreVerificationGate",
]

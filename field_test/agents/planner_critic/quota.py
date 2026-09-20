from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from planner_critic.gates.base import BaseGate
from planner_critic.reason_codes import (
    BLAST_RADIUS_QUOTA_BREACH,
    BLAST_RADIUS_RESTRICTED_ACTION,
    BLAST_RADIUS_RESTRICTED_CLUSTER,
)
from planner_critic.schema.goal import RiskTolerance
from planner_critic.schema.plan import PlanVersion
from planner_critic.types import Finding, Severity

logger = logging.getLogger(__name__)


QUOTA_DEFAULTS: dict[str, object] = {
    "max_resource_changes": None,
    "max_destructive_actions": None,
    "max_database_alterations": None,
    "restricted_clusters": [],
    "restricted_actions": [],
}


@dataclass
class BlastRadiusQuotaConfig:
    max_resource_changes: int | None = None
    max_destructive_actions: int | None = None
    max_database_alterations: int | None = None
    restricted_clusters: list[str] = field(default_factory=list)
    restricted_actions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlastRadiusQuotaConfig:
        return cls(
            max_resource_changes=data.get("max_resource_changes"),
            max_destructive_actions=data.get("max_destructive_actions"),
            max_database_alterations=data.get("max_database_alterations"),
            restricted_clusters=list(data.get("restricted_clusters", [])),
            restricted_actions=list(data.get("restricted_actions", [])),
        )


_DESTRUCTIVE_ACTIONS = {"delete", "destroy", "drop", "terminate", "remove"}
_DB_ALTERATIVE_ACTIONS = {"drop", "alter", "migrate", "truncate", "delete", "destroy"}


class BlastRadiusQuotaGate(BaseGate):
    name: str = "blast_radius_quota"

    def __init__(
        self,
        config: BlastRadiusQuotaConfig,
        posture: RiskTolerance = RiskTolerance.BALANCED,
    ) -> None:
        self._config = config
        self._posture = posture

    def run(self, plan: PlanVersion) -> list[Finding]:
        findings: list[Finding] = []
        resource_count = 0
        destructive_count = 0
        db_alterations = 0

        for task in plan.tasks:
            resource_count += 1
            target_lower = (task.target or "").lower()
            action_lower = (task.action or "").lower()

            if action_lower in _DESTRUCTIVE_ACTIONS:
                destructive_count += 1
            is_db_target = (
                "schema" in target_lower or "database" in target_lower or "db" in target_lower
            )
            if is_db_target and action_lower in _DB_ALTERATIVE_ACTIONS:
                db_alterations += 1

            if self._config.restricted_actions:
                for restricted in self._config.restricted_actions:
                    if action_lower == restricted.lower():
                        is_strict = self._posture is RiskTolerance.STRICT
                        findings.append(
                            Finding(
                                id=f"quota:restricted_action:{task.id}",
                                task_id=task.id,
                                version=plan.version,
                                severity=Severity.BLOCKER if is_strict else Severity.WARNING,
                                reason_code=BLAST_RADIUS_RESTRICTED_ACTION,
                                message=f"Task {task.id} uses restricted action {restricted!r}",
                            )
                        )

            target_for_cluster = (task.target or "").lower()
            for cluster in self._config.restricted_clusters:
                cluster_lower = cluster.lower()
                if target_for_cluster == cluster_lower or target_for_cluster.endswith(
                    f"-{cluster_lower}"
                ):
                    is_strict = self._posture is RiskTolerance.STRICT
                    findings.append(
                        Finding(
                            id=f"quota:restricted_cluster:{task.id}",
                            task_id=task.id,
                            version=plan.version,
                            severity=Severity.BLOCKER if is_strict else Severity.WARNING,
                            reason_code=BLAST_RADIUS_RESTRICTED_CLUSTER,
                            message=f"Task {task.id} targets restricted cluster {cluster!r}",
                        )
                    )

        if (
            self._config.max_resource_changes is not None
            and resource_count > self._config.max_resource_changes
        ):
            findings.append(
                Finding(
                    id="quota:max_resource_changes",
                    task_id=None,
                    version=plan.version,
                    severity=Severity.BLOCKER,
                    reason_code=BLAST_RADIUS_QUOTA_BREACH,
                    message=(
                        f"Plan modifies {resource_count} resources, "
                        f"exceeds quota max_resource_changes={self._config.max_resource_changes}"
                    ),
                )
            )

        if (
            self._config.max_destructive_actions is not None
            and destructive_count > self._config.max_destructive_actions
        ):
            findings.append(
                Finding(
                    id="quota:max_destructive_actions",
                    task_id=None,
                    version=plan.version,
                    severity=Severity.BLOCKER,
                    reason_code=BLAST_RADIUS_QUOTA_BREACH,
                    message=(
                        f"Plan has {destructive_count} destructive actions, "
                        f"exceeds quota max_destructive_actions="
                        f"{self._config.max_destructive_actions}"
                    ),
                )
            )

        if (
            self._config.max_database_alterations is not None
            and db_alterations > self._config.max_database_alterations
        ):
            findings.append(
                Finding(
                    id="quota:max_database_alterations",
                    task_id=None,
                    version=plan.version,
                    severity=Severity.BLOCKER,
                    reason_code=BLAST_RADIUS_QUOTA_BREACH,
                    message=(
                        f"Plan has {db_alterations} database alterations, "
                        f"exceeds quota max_database_alterations="
                        f"{self._config.max_database_alterations}"
                    ),
                )
            )

        return findings

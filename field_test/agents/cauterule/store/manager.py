"""Store manager — CRUD for standing rules on the file system."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from pathlib import Path

from cauterule.models.rule import StandingRule
from cauterule.serialization.rule_yaml import (
    dump_rule_to_file_atomic,
    load_rule_from_file,
    load_rules_from_dir,
)

# Allowlist for rule ids used in filesystem paths (#499). Rule ids are
# program-generated (`R-001`-style); anything outside this set is rejected
# before any I/O happens.
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_rule_id(rule_id: str) -> None:
    """Reject rule ids unsafe for filesystem paths (#499).

    Raises:
        ValueError: If *rule_id* is outside the allowlist or the
            resolved path escapes *base_dir*.
    """
    if not isinstance(rule_id, str) or not _RULE_ID_RE.match(rule_id):
        msg = f"invalid rule_id {rule_id!r}"
        raise ValueError(msg)


def resolve_inside(base_dir: Path, *parts: str) -> Path:
    """Join *parts* onto *base_dir* and assert containment (#499).

    Raises:
        ValueError: If the resolved path escapes *base_dir*.
    """
    path = (base_dir.joinpath(*parts)).resolve()
    if not path.is_relative_to(base_dir.resolve()):
        msg = f"path escapes directory {str(base_dir)!r}: {parts!r}"
        raise ValueError(msg)
    return path


class StoreManager:
    """File-system CRUD for :class:`StandingRule` instances.

    Each rule is persisted as an individual YAML file named ``<id>.yaml``
    inside *base_dir*.
    """

    def __init__(self, base_dir: str = "rules") -> None:
        """StoreManager(*base_dir*).

        Args:
            base_dir: Directory holding rule files.
        """
        self.base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _rule_path(self, rule_id: str) -> Path:
        """Return the file path for *rule_id*, rejecting traversal (#499).

        Single choke point for all rule file I/O (get/add/retire/supersede
        plus observe callers). Raises:
            ValueError: If *rule_id* is outside the allowlist or the
                resolved path escapes *base_dir*.
        """
        validate_rule_id(rule_id)
        return resolve_inside(self.base_dir, f"{rule_id}.yaml")

    def _ensure_dir(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_rule(self, rule_id: str) -> StandingRule | None:
        """Return the rule with *rule_id*, or ``None`` if it does not exist."""
        path = self._rule_path(rule_id)
        if not path.is_file():
            return None
        return load_rule_from_file(path)

    def list_rules(self, status: str | None = None) -> list[StandingRule]:
        """Return all rules, optionally filtered by *status*.

        Args:
            status: Optional status filter (``"active"``, ``"retired"``, etc.).

        Returns:
            List of :class:`StandingRule` instances.
        """
        self._ensure_dir()
        rules = load_rules_from_dir(self.base_dir)
        if status is not None:
            rules = [r for r in rules if r.status == status]
        return rules

    def add_rule(self, rule: StandingRule) -> str:
        """Persist *rule* to disk and return its id.

        Args:
            rule: The rule to persist.

        Returns:
            The rule's id.
        """
        self._ensure_dir()
        dump_rule_to_file_atomic(rule, self._rule_path(rule.id))
        return rule.id

    def retire_rule(self, rule_id: str, reason: str) -> None:
        """Mark a rule as retired.

        Args:
            rule_id: Id of the rule to retire.
            reason: Reason for retirement.
        """
        rule = self.get_rule(rule_id)
        if rule is None:
            msg = f"Rule {rule_id!r} not found"
            raise ValueError(msg)
        if rule.status != "active":
            msg = f"Cannot retire rule {rule_id!r}: status is {rule.status!r}"
            raise ValueError(msg)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        retired = replace(
            rule,
            status="retired",
            retired_at=now,
            retirement_reason=reason,
        )
        dump_rule_to_file_atomic(retired, self._rule_path(rule_id))
        _index_sync_safe(retired, self.base_dir)
        _commit_safe(f"retire rule {rule_id}: {reason}", str(self.base_dir))
        # OTEL rule.retire span (#588): best-effort, never blocks retirement.
        try:
            from cauterule.integrations.otel import OtelExporter

            OtelExporter().emit_rule_retire(rule_id, reason=reason)
        except Exception:
            pass

    def supersede_rule(self, rule_id: str, new_id: str) -> None:
        """Mark *rule_id* as superseded by *new_id*.

        The existing rule is re-written with status ``"superseded"``.

        Args:
            rule_id: Id of the rule to supersede.
            new_id: Id of the rule that replaces it.
        """
        rule = self.get_rule(rule_id)
        if rule is None:
            msg = f"Rule {rule_id!r} not found"
            raise ValueError(msg)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        superseded = replace(
            rule,
            status="superseded",
            retired_at=now,
            superseded_by=new_id,
        )
        dump_rule_to_file_atomic(superseded, self._rule_path(rule_id))
        _index_sync_safe(superseded, self.base_dir)
        _commit_safe(
            f"supersede rule {rule_id} (replaced by {new_id})",
            str(self.base_dir),
        )


# ------------------------------------------------------------------
# Helpers — best-effort index + git sync (failure is logged, not raised)
# ------------------------------------------------------------------
def _index_sync_safe(
    rule: StandingRule,
    base_dir: Path,
) -> None:
    try:
        from cauterule.store.index import IndexManager

        IndexManager(str(base_dir)).update_entry(rule)
    except Exception:
        pass


def _commit_safe(message: str, base_dir: str) -> None:
    try:
        from cauterule.store.git import git_commit

        git_commit(message, base_dir)
    except Exception:
        pass

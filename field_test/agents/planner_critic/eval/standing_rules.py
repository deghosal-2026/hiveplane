"""Standing-rule promotion from missed-critique analysis (M5, #127).

Consumes missed records from the oracle eval harness and proposes candidate
deterministic standing rules derived from (CWE x patch-pattern). High-trust
misses (directly linked to ground truth) can be promoted to heuristic-pack
rules with full provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..reason_codes import ReasonCode


@dataclass
class StandingRule:
    """A candidate or promoted standing rule.

    Attributes:
        rule_id: Unique identifier (e.g. ``SR-CWE-079-MISSING-VERIFICATION``).
        cwe_bucket: The CWE bucket this rule applies to.
        pattern: The patch pattern (e.g. ``output_encoding``).
        heuristic_family: Which heuristic family the rule targets.
        reason_code: The reason code the rule should produce.
        trust: ``high`` (from oracle eval misses) or ``low`` (from stub exec).
        source_instance_ids: Corpus instances that generated this miss.
        coverage_count: Number of times this (CWE x pattern) was seen.
        status: ``proposed`` or ``promoted`` (once committed to pack).
        promoted_at: When the rule was promoted.
        provenance: Free-form provenance data.
    """

    rule_id: str
    cwe_bucket: str
    pattern: str
    heuristic_family: str
    reason_code: ReasonCode | str
    trust: str = "high"
    source_instance_ids: list[str] = field(default_factory=list)
    coverage_count: int = 1
    status: str = "proposed"
    promoted_at: datetime | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


# Pattern templates derived from common CWE fix patterns.
CWE_PATTERN_MAP: dict[str, list[str]] = {
    "XSS": ["output_encoding", "html_escape", "template_sanitization"],
    "SQLI": ["parameterized_query", "query_parameterization", "input_validation"],
    "PATH_TRAVERSAL": ["path_canonicalization", "path_allowlist"],
    "AUTH": ["role_check", "acl_middleware", "authorization_decorator"],
    "DESERIALIZATION": ["safe_deserialization", "schema_validation"],
    "SSRF": ["url_allowlist", "private_ip_blocklist"],
    "SECRETS": ["credential_extraction", "secrets_migration"],
}

HEURISTIC_FAMILY_FOR_BUCKET: dict[str, str] = {
    "XSS": "missing_steps",
    "SQLI": "missing_steps",
    "PATH_TRAVERSAL": "missing_steps",
    "AUTH": "unsafe_sequencing",
    "DESERIALIZATION": "weak_rollback",
    "SSRF": "unverified_dependencies",
    "SECRETS": "unverified_dependencies",
}


class StandingRuleRegistry:
    """Registry of proposed and promoted standing rules.

    Tracks rules by (CWE_bucket, pattern) dedup key and supports
    propose → promote lifecycle.
    """

    def __init__(self) -> None:
        self._rules: dict[str, StandingRule] = {}
        self._next_id = 1

    def propose_from_misses(
        self,
        cwe_bucket: str,
        instance_ids: list[str],
        missed_reason_codes: list[str],
    ) -> list[StandingRule]:
        """Propose candidate standing rules from missed-critique records.

        Args:
            cwe_bucket: The CWE bucket that produced the misses.
            instance_ids: Source corpus instance IDs.
            missed_reason_codes: Reason codes the critic missed.

        Returns:
            Newly proposed rules (may be empty if patterns already exist).
        """
        patterns = CWE_PATTERN_MAP.get(cwe_bucket, [])
        heuristic = HEURISTIC_FAMILY_FOR_BUCKET.get(cwe_bucket, "missing_steps")
        proposed: list[StandingRule] = []

        for pattern in patterns:
            dedup_key = f"{cwe_bucket}:{pattern}"
            existing = self._rules.get(dedup_key)

            if existing:
                existing.coverage_count += 1
                for iid in instance_ids:
                    if iid not in existing.source_instance_ids:
                        existing.source_instance_ids.append(iid)
                continue

            rule = StandingRule(
                rule_id=f"SR-{cwe_bucket}-{pattern.upper()}",
                cwe_bucket=cwe_bucket,
                pattern=pattern,
                heuristic_family=heuristic,
                reason_code=missed_reason_codes[0] if missed_reason_codes else "",
                source_instance_ids=list(instance_ids),
                coverage_count=1,
                status="proposed",
            )
            self._rules[dedup_key] = rule
            proposed.append(rule)

        return proposed

    def promote(self, rule_id: str) -> bool:
        """Promote a proposed rule to active status.

        Args:
            rule_id: The rule's ``rule_id`` field.

        Returns:
            True if the rule was found and promoted; False if not found or
            already promoted.
        """
        for _dedup_key, rule in self._rules.items():
            if rule.rule_id == rule_id:
                if rule.status == "promoted":
                    return False
                rule.status = "promoted"
                rule.promoted_at = datetime.now(UTC)
                return True
        return False

    def list_rules(self, status: str | None = None) -> list[StandingRule]:
        """List all rules, optionally filtered by status.

        Args:
            status: ``proposed``, ``promoted``, or None for all.

        Returns:
            A sorted list of matching rules.
        """
        rules = list(self._rules.values())
        if status:
            rules = [r for r in rules if r.status == status]
        return sorted(rules, key=lambda r: (r.cwe_bucket, r.pattern))

    def get_rule_ids_with_status(self, status: str | None = None) -> list[str]:
        """Return rule IDs matching the given status filter."""
        return [r.rule_id for r in self.list_rules(status=status)]


__all__ = [
    "CWE_PATTERN_MAP",
    "HEURISTIC_FAMILY_FOR_BUCKET",
    "StandingRule",
    "StandingRuleRegistry",
]

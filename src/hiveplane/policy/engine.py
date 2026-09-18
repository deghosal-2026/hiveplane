"""Context-aware, deny-by-default policy evaluation (DD-03)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from opentelemetry.util.types import AttributeValue

from hiveplane import metrics, telemetry
from hiveplane.certification.models import CertificationStatus
from hiveplane.core.decision import (
    ActionClass,
    BlastRadius,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
    PolicyDecision,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.policy.models import PolicyPackOverride
from hiveplane.policy.packs import PolicyPackStore


def compute_blast_radius(context: PolicyContext) -> BlastRadius:
    """Compute a blast-radius score and its factor contributions."""
    factors: dict[str, int] = {}
    if context.tool_trust is ToolTrustLevel.DESTRUCTIVE:
        factors["tool_trust"] = 35
    if context.environment is AdmissionContext.PRODUCTION:
        factors["environment"] = 50
    elif context.environment is AdmissionContext.STAGING:
        factors["environment"] = 15
    if context.data_sensitivity is DataSensitivity.RESTRICTED:
        factors["data_sensitivity"] = 20
    elif context.data_sensitivity is DataSensitivity.PII:
        factors["data_sensitivity"] = 15
    elif context.data_sensitivity is DataSensitivity.INTERNAL:
        factors["data_sensitivity"] = 5
    if context.action_class in (ActionClass.PRODUCTION_WRITE, ActionClass.DESTRUCTIVE):
        factors["action_class"] = 20
    elif context.action_class is ActionClass.HIGH_SPEND:
        factors["action_class"] = 10
    if context.certification_status is CertificationStatus.UNCERTIFIED:
        factors["certification"] = 15
    elif context.certification_status is CertificationStatus.PROVISIONAL:
        factors["certification"] = 8
    return BlastRadius(score=min(100, sum(factors.values())), factors=factors)


def _policy_attributes(context: PolicyContext) -> dict[str, AttributeValue]:
    """Build the correlation attributes for a policy-decision span."""
    attributes: dict[str, AttributeValue] = {
        telemetry.RUN_ID: context.run_id,
        telemetry.WORKLOAD: context.workload,
        "environment": context.environment.value,
        telemetry.CERTIFICATION_STATUS: context.certification_status.value,
    }
    if context.team is not None:
        attributes[telemetry.TEAM] = context.team
    if context.tool_id is not None:
        attributes["tool_id"] = context.tool_id
    if context.action_class is not None:
        attributes["action_class"] = context.action_class.value
    return attributes


class PolicyEngine:
    """Evaluates policy for a run or tool call, with explainable decisions."""

    def __init__(
        self, packs: PolicyPackStore, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.packs = packs
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        """Return the policy decision for a context, as a ``policy_decision`` span."""
        with telemetry.span(
            "policy_decision", attributes=_policy_attributes(context)
        ) as active:
            decision = self._evaluate(context)
            active.set_attribute("decision", decision.outcome.value)
            active.set_attribute("rule", decision.rule)
            metrics.get_metrics().record_policy_decision(
                workload=context.workload,
                team=context.team,
                decision=decision.outcome.value,
                rule=decision.rule,
            )
            return decision

    def _evaluate(self, context: PolicyContext) -> PolicyDecision:
        """Return the policy decision for a context."""
        blast = compute_blast_radius(context)
        if context.certification_status is CertificationStatus.QUARANTINED:
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "certification.quarantined",
                "workload is quarantined",
                blast,
            )
        if context.injection_detected:
            return self._decide(
                context,
                DecisionOutcome.BLOCK_INJECTION,
                "injection.scan",
                "tool output contains injection patterns",
                blast,
            )
        if context.tool_id is None:
            packed = self._pack_decision(context, blast)
            if packed is not None:
                return packed
            return self._decide(
                context, DecisionOutcome.ALLOW, "run.allow", "no tool policy applies", blast
            )

        tools = context.tools
        if tools is not None and tools.is_denied(context.tool_id):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "manifest.deny",
                f"tool {context.tool_id!r} is explicitly denied",
                blast,
            )
        packed = self._pack_decision(context, blast)
        if packed is not None:
            return packed
        if (
            context.data_sensitivity is DataSensitivity.RESTRICTED
            and context.action_class is not None
            and context.action_class is not ActionClass.READ_ONLY
        ):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "sensitivity.restricted",
                "restricted data forbids write actions",
                blast,
            )
        if (
            context.data_sensitivity is DataSensitivity.RESTRICTED
            and context.action_class is ActionClass.READ_ONLY
        ):
            return self._decide(
                context,
                DecisionOutcome.ESCALATE,
                "sensitivity.restricted.read",
                "read-only access to restricted data requires approval",
                blast,
            )
        if (
            context.data_sensitivity is DataSensitivity.PII
            and context.tool_trust is ToolTrustLevel.DESTRUCTIVE
        ):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "sensitivity.pii",
                "PII forbids destructive tools",
                blast,
            )

        if tools is None or not tools.is_allowed(context.tool_id):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "default.deny",
                f"tool {context.tool_id!r} is not allowed",
                blast,
            )
        if context.environment is AdmissionContext.SANDBOX:
            return self._decide(
                context,
                DecisionOutcome.ALLOW,
                "sandbox.allow",
                "sandbox context allows the tool",
                blast,
            )
        if (
            context.environment is AdmissionContext.PRODUCTION
            and context.certification_status is not CertificationStatus.CERTIFIED
        ):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "certification.production",
                "production requires a certified workload",
                blast,
            )
        if context.tool_trust is ToolTrustLevel.DESTRUCTIVE:
            return self._decide(
                context,
                DecisionOutcome.ESCALATE,
                "trust.destructive",
                "destructive tools require approval",
                blast,
            )
        if tools.approval_required(context.tool_id) or (
            context.action_class is not None
            and context.action_class in context.approval_required_for
        ):
            return self._decide(
                context,
                DecisionOutcome.ESCALATE,
                "approvals.required",
                "action requires approval",
                blast,
            )
        if blast.score >= 71:
            if (
                context.environment is AdmissionContext.PRODUCTION
                and context.certification_status is CertificationStatus.CERTIFIED
            ):
                return self._decide(
                    context,
                    DecisionOutcome.ALLOW,
                    "blast_radius.high.certified",
                    "high blast radius permitted by production certification",
                    blast,
                )
            return self._decide(
                context, DecisionOutcome.DENY, "blast_radius.high", "blast radius is high", blast
            )
        if blast.score >= 31:
            return self._decide(
                context,
                DecisionOutcome.ESCALATE,
                "blast_radius.medium",
                "blast radius is medium",
                blast,
            )
        return self._decide(
            context, DecisionOutcome.ALLOW, "manifest.allow", "tool is explicitly allowed", blast
        )

    def _pack_decision(self, context: PolicyContext, blast: BlastRadius) -> PolicyDecision | None:
        for pack in self.packs.for_team(context.team):
            for override in pack.spec.overrides:
                if not self._matches(override, context):
                    continue
                for rule in override.rules:
                    if rule.action is DecisionOutcome.ALLOW:
                        continue
                    if (
                        rule.action_class is not None
                        and rule.action_class is not context.action_class
                    ):
                        continue
                    if rule.tool_trust is not None and rule.tool_trust is not context.tool_trust:
                        continue
                    if rule.action is DecisionOutcome.DENY:
                        rule_id = "pack.deny"
                    else:
                        rule_id = "pack.escalate"
                    return self._decide(
                        context,
                        rule.action,
                        rule_id,
                        f"policy pack {pack.metadata.name!r} applies",
                        blast,
                    )
        return None

    @staticmethod
    def _matches(override: PolicyPackOverride, context: PolicyContext) -> bool:
        match = override.match
        if match.environment is not None and match.environment is not context.environment:
            return False
        return not (
            match.data_sensitivity is not None
            and match.data_sensitivity is not context.data_sensitivity
        )

    def _decide(
        self,
        context: PolicyContext,
        outcome: DecisionOutcome,
        rule: str,
        reason: str,
        blast: BlastRadius,
    ) -> PolicyDecision:
        return PolicyDecision(
            run_id=context.run_id,
            outcome=outcome,
            rule=rule,
            reason=reason,
            action_class=context.action_class,
            timestamp=self._clock(),
            blast_radius=blast,
            certification_status=context.certification_status,
        )

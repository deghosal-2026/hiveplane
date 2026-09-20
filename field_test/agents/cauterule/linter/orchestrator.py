"""Linter orchestrator — runs all checks, produces LinterResult."""

from __future__ import annotations

from dataclasses import dataclass, field

from cauterule.linter.contradiction import check_contradiction
from cauterule.linter.duplicate import check_duplicate
from cauterule.linter.specificity import check_broadness, check_specificity
from cauterule.linter.tautology import check_tautology
from cauterule.linter.unsafe import check_unsafe
from cauterule.linter.untestable import check_untestable
from cauterule.linter.vagueness import check_vagueness
from cauterule.models.rule import StandingRule


@dataclass(frozen=True)
class LinterResult:
    """Result of running all linter checks."""

    warnings: tuple[str, ...] = field(default_factory=tuple)
    passed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "passed", len(self.warnings) == 0)


def lint_rule(
    trigger: str,
    directive: str,
    existing_rules: list[StandingRule] | None = None,
    context: tuple[str, ...] = (),
) -> LinterResult:
    """Run all linter checks on a candidate rule.

    Args:
        trigger: The ``when`` trigger.
        directive: The ``do`` directive.
        existing_rules: Existing rules for duplicate/contradiction checks.
        context: Context items from ``when.context``.

    Returns:
        :class:`LinterResult` with warnings and pass/fail.
    """
    warnings: list[str] = []
    warnings.extend(check_vagueness(trigger))
    warnings.extend(check_specificity(trigger))
    warnings.extend(check_broadness(trigger))
    warnings.extend(check_vagueness(directive))
    for ctx in context:
        warnings.extend(check_vagueness(ctx))
    warnings.extend(check_tautology(trigger, directive))
    warnings.extend(check_untestable(directive))
    warnings.extend(check_unsafe(directive))
    if existing_rules:
        warnings.extend(check_duplicate(trigger, directive, existing_rules))
        warnings.extend(check_contradiction(trigger, directive, existing_rules))
    return LinterResult(warnings=tuple(warnings))

"""Rule templates — retry, verify-then-act, check-preconditions."""

from __future__ import annotations

from typing import Any

TEMPLATES: dict[str, str] = {
    "retry": (
        "When {trigger}\n"
        "  Context: {context}\n"
        "  Do: Retry the operation after a brief delay. "
        "If the failure persists after {max_retries} attempts, escalate.\n"
        "  Because: Transient errors are common in {domain} and a simple retry "
        "often resolves them without manual intervention."
    ),
    "verify-then-act": (
        "When {trigger}\n"
        "  Context: {context}\n"
        "  Do: First verify the preconditions for {action} are met. "
        "Check {verification_steps}. Only proceed if all checks pass.\n"
        "  Because: Acting without verification in {domain} can lead to "
        "irreversible state changes or data loss."
    ),
    "check-preconditions": (
        "When {trigger}\n"
        "  Context: {context}\n"
        "  Do: Before proceeding, ensure: {preconditions}. "
        "If any precondition is not met, halt and report the issue.\n"
        "  Because: {domain} operations have hard dependencies that must be "
        "satisfied before execution."
    ),
}


def apply_template(template: str, trigger: str, directive: str, **kwargs: Any) -> str:
    """Fill in a named template with provided values.

    Built-in templates:

    - ``"retry"`` — Retry the operation after a brief delay.
    - ``"verify-then-act"`` — Verify preconditions before acting.
    - ``"check-preconditions"`` — Check dependencies before proceeding.

    Args:
        template: Template name (e.g. ``"retry"``) or a custom format string.
        trigger: The trigger phrase for the rule.
        directive: The action directive for the rule.
        **kwargs: Additional variables to fill into the template
            (e.g. ``domain``, ``max_retries``, ``context``).

    Returns:
        The filled template string.

    Raises:
        KeyError: If a template variable referenced in the format string
            is missing from *kwargs*.
    """
    template_str = TEMPLATES.get(template, template)
    ctx: dict[str, Any] = {"trigger": trigger, "directive": directive}
    ctx.update(kwargs)
    return template_str.format(**ctx)

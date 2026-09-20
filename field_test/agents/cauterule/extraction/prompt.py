"""Extraction prompt builder."""

from __future__ import annotations

from cauterule.models.trajectory import Trajectory

TEMPLATE_INSTRUCTIONS: dict[str, str] = {
    "retry": "Suggest a retry-with-fix pattern.",
    "verify-then-act": "Suggest a verify-before-act pattern.",
    "check-preconditions": "Suggest a check-preconditions pattern.",
}


def build_extraction_prompt(trajectory: Trajectory, template: str | None = None) -> str:
    """Build an extraction prompt for *trajectory*.

    Args:
        trajectory: Trajectory to extract from.
        template: Optional template hint (retry, verify-then-act, check-preconditions).
    """
    steps_str = "\n".join(
        f"Step {s.step_number}: tool={s.tool} input={s.input or ''} output={s.output or ''} error={s.error or ''}"
        for s in trajectory.steps
    )
    template_hint = ""
    if template:
        hint = TEMPLATE_INSTRUCTIONS.get(template, template)
        template_hint = f"\nTemplate hint: {template} — {hint}"

    failure_point = trajectory.failure_point or "unknown"
    failure_class = trajectory.failure_class or "unknown"

    return (
        f"You are an expert at extracting actionable standing rules from agent failures.\n"
        f"Task: {trajectory.task}\n"
        f"Failure point: {failure_point}\n"
        f"Failure class: {failure_class}\n"
        f"Steps:\n{steps_str}\n"
        f"Tags: {', '.join(trajectory.tags) if trajectory.tags else 'none'}\n"
        f"{template_hint}\n"
        f"\nExtract a single standing rule from this failure. "
        f"Return ONLY a JSON object with these exact keys:\n"
        f'- when: object with "trigger" (string), "context" (array of strings), '
        f'and "error_signature" (string, the normalized exception type / error code / '
        f'exit code — e.g. "ModuleNotFoundError", "non-fast-forward", "exit 128")\n'
        f'- do: object with "directive" (string) and "because" (string)\n'
        f"- confidence: number between 0.0 and 1.0\n"
        f"- reasoning: string\n"
        f"\nCRITICAL: The trigger MUST name the specific error code, error message, "
        f"or failure signature — not just the tool name. "
        f'For example, write "when git push fails with non-fast-forward" '
        f'NOT "when git push fails". '
        f"Set error_signature to that distinguishing token, normalized."
        f"\nDo not include any text before or after the JSON object.\n"
        f"Context entries must be non-empty strings or omit the context array entirely.\n"
        f"\nExample output:\n"
        f'{{"when": {{"trigger": "when git push fails with non-fast-forward", '
        f'"context": ["git", "push", "rejected"], '
        f'"error_signature": "non-fast-forward"}}, '
        f'"do": {{"directive": "pull latest changes before pushing", '
        f'"because": "remote has commits not in local branch"}}, '
        f'"confidence": 0.85, '
        f'"reasoning": "The push was rejected because the remote branch has newer commits. '
        f'Pulling first would fast-forward the local branch and allow the push to succeed."}}'
    )

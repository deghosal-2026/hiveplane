"""LLM extraction call."""

from __future__ import annotations

import json
from typing import Any

from cauterule.extraction.prompt import build_extraction_prompt
from cauterule.extraction.quality import check_quality
from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen
from cauterule.models.trajectory import Trajectory


def _extract_first_json_object(text: str) -> str:
    """Extract the first balanced JSON object from *text*.

    Handles trailing commentary, multiple objects, and prose around JSON.
    Returns the substring for the first complete ``{ ... }`` object.
    Raises ``ValueError`` if no balanced object is found.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM output")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("No balanced JSON object found in LLM output")


def _parse_candidate_json(
    text: str, extraction_pass: int = 1, template: str | None = None
) -> CandidateRule:
    """Parse LLM output JSON into a :class:`CandidateRule`."""
    json_str = _extract_first_json_object(text)
    data = json.loads(json_str)
    if not isinstance(data, dict):
        raise ValueError("LLM output must be a JSON object")

    when_data = data.get("when", {})
    do_data = data.get("do", {})
    if not isinstance(when_data, dict) or not isinstance(do_data, dict):
        raise ValueError("when/do must be objects")

    # Filter out blank/None context entries before validation
    raw_context = when_data.get("context", [])
    if not isinstance(raw_context, list):
        raw_context = []
    context_items = tuple(str(c).strip() for c in raw_context if c and str(c).strip())

    # #725: structured error signature (exception type / error code), optional.
    raw_signature = when_data.get("error_signature")
    signature = str(raw_signature).strip() if raw_signature and str(raw_signature).strip() else None

    when = RuleWhen(
        trigger=str(when_data.get("trigger", "")),
        context=context_items,
        signature=signature,
    )
    do = RuleDo(
        directive=str(do_data.get("directive", "")),
        because=do_data.get("because"),
    )
    confidence = float(data.get("confidence", 0.5))
    reasoning = data.get("reasoning")
    tmpl = data.get("template", template)

    return CandidateRule(
        when=when,
        do=do,
        confidence=confidence,
        reasoning=str(reasoning) if reasoning is not None else None,
        extraction_pass=extraction_pass,
        template=str(tmpl) if tmpl is not None else None,
    )


def extract_candidate(
    trajectory: Trajectory,
    llm: Any,
    template: str | None = None,
    extraction_pass: int = 1,
    temperature: float = 0.5,
    confidence_threshold: float = 0.6,
) -> CandidateRule:
    """Call LLM to extract a candidate rule from *trajectory*.

    Args:
        trajectory: Trajectory to extract from.
        llm: LLM provider with ``complete(prompt)`` method.
        template: Optional template hint.
        extraction_pass: Pass number (1-indexed).
        temperature: LLM temperature for this pass.
        confidence_threshold: Minimum quality-gate confidence (defaults to
            ``ExtractionConfig.confidence_threshold``; #497).

    Raises:
        ValueError: If LLM output cannot be parsed or fails quality checks with hard fail
            (caller may decide to fallback to human review).
    """
    prompt = build_extraction_prompt(trajectory, template=template)
    result = llm.complete(prompt, temperature=temperature)
    text = result.text if hasattr(result, "text") else str(result)
    candidate = _parse_candidate_json(text, extraction_pass=extraction_pass, template=template)

    warnings = check_quality(candidate, trajectory, confidence_threshold)
    if warnings:
        msg = f"candidate failed quality gate: {'; '.join(warnings)}"
        raise ValueError(msg)
    return candidate


def extract_candidate_safe(
    trajectory: Trajectory,
    llm: Any,
    template: str | None = None,
    extraction_pass: int = 1,
    temperature: float = 0.5,
    confidence_threshold: float = 0.6,
) -> tuple[CandidateRule | None, str | None]:
    """Safe wrapper that returns (candidate, error) instead of raising."""
    try:
        candidate = extract_candidate(
            trajectory,
            llm,
            template=template,
            extraction_pass=extraction_pass,
            temperature=temperature,
            confidence_threshold=confidence_threshold,
        )
        return candidate, None
    except Exception as exc:
        return None, str(exc)

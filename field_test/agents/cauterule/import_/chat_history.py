"""Import rules from chat history files.

Parses chat/conversation logs to extract candidate rules by looking for
patterns where a user gives instructions or feedback that can be turned
into standing rules.
"""

from __future__ import annotations

import re
from pathlib import Path

from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen

_RULE_PATTERN = re.compile(
    r"(?:(?:rule|convention|policy|guideline|remember)\s*(?::|is|to|that)?)"
    r"\s*(?:when|if|whenever)\s+(.+?)\s*(?:,|\.|then)\s+(.+?)(?:\n|\.|$)",
    re.IGNORECASE | re.DOTALL,
)


def import_chat_history(path: str) -> list[CandidateRule]:
    """Parse *path* (a chat history file) into CandidateRules.

    Uses heuristics to extract rule-like statements from conversation text.
    """
    p = Path(path)
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8")

    candidates: list[CandidateRule] = []

    for match in _RULE_PATTERN.finditer(text):
        trigger = match.group(1).strip()
        directive = match.group(2).strip()
        if trigger and directive:
            candidates.append(
                CandidateRule(
                    when=RuleWhen(trigger=trigger),
                    do=RuleDo(directive=directive),
                    confidence=0.4,
                )
            )

    return candidates

"""Import rules from agent convention files.

Parses .cursorrules, CLAUDE.md, and AGENTS.md files back into CandidateRule
objects by extracting structured rule (when/do) pairs from Markdown.
"""

from __future__ import annotations

import re
from pathlib import Path

from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen

_WHEN_PATTERN = re.compile(
    r"(?:\*\*When:?\*\*|\*\*When\*\*|When\s+|`When`)\s*(.+?)(?:\s+→|\s*$)",
    re.IGNORECASE,
)
_DO_PATTERN = re.compile(
    r"(?:\*\*Do:?\*\*|\*\*Do\*\*|Do\s+|`Do`)\s*(.+?)$",
    re.IGNORECASE,
)
_BULLET_WHEN = re.compile(
    r"\*\*When\*\*\s+(.+?)\s+→\s+\*\*Do\*\*\s+(.+)",
    re.IGNORECASE,
)
_BULLET_WHEN_BACKTICK = re.compile(
    r"When\s+`(.+?)`\s+then\s+\*\*(.+?)\*\*",
    re.IGNORECASE,
)


def _extract_from_line(line: str) -> CandidateRule | None:
    """Try to extract a CandidateRule from a single line."""
    m = _BULLET_WHEN.match(line)
    if m:
        return CandidateRule(
            when=RuleWhen(trigger=m.group(1).strip()),
            do=RuleDo(directive=m.group(2).strip()),
            confidence=0.5,
        )
    m = _BULLET_WHEN_BACKTICK.match(line)
    if m:
        return CandidateRule(
            when=RuleWhen(trigger=m.group(1).strip()),
            do=RuleDo(directive=m.group(2).strip()),
            confidence=0.5,
        )
    return None


def import_conventions(path: str) -> list[CandidateRule]:
    """Parse *path* (a Markdown convention file) into CandidateRules."""
    p = Path(path)
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8")

    candidates: list[CandidateRule] = []
    pending_trigger: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        candidate = _extract_from_line(stripped)
        if candidate is not None:
            candidates.append(candidate)
            pending_trigger = None
            continue

        when_m = _WHEN_PATTERN.search(stripped)
        do_m = _DO_PATTERN.search(stripped)

        if when_m and do_m:
            trigger = when_m.group(1).strip()
            directive = do_m.group(1).strip()
            candidates.append(
                CandidateRule(
                    when=RuleWhen(trigger=trigger),
                    do=RuleDo(directive=directive),
                    confidence=0.5,
                )
            )
            pending_trigger = None
        elif when_m and not do_m:
            pending_trigger = when_m.group(1).strip()
        elif do_m and pending_trigger is not None:
            candidates.append(
                CandidateRule(
                    when=RuleWhen(trigger=pending_trigger),
                    do=RuleDo(directive=do_m.group(1).strip()),
                    confidence=0.5,
                )
            )
            pending_trigger = None
        else:
            pending_trigger = None

    return candidates

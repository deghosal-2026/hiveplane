from __future__ import annotations
import re
from typing import Any

from state import ConventionalCommit, CommitType

TYPE_MAP: dict[str, CommitType] = {
    "feat": CommitType.feat,
    "fix": CommitType.fix,
    "docs": CommitType.docs,
    "chore": CommitType.chore,
    "test": CommitType.test,
    "perf": CommitType.perf,
    "style": CommitType.style,
    "build": CommitType.build,
    "ci": CommitType.ci,
    "revert": CommitType.revert,
    "refactor": CommitType.refactor,
}

CONVENTIONAL_PATTERN = re.compile(
    r"^(?P<type>[a-zA-Z]+)"
    r"(?:\((?P<scope>[^)]*)\))?"
    r"(?P<breaking>!)?\s*:\s*"
    r"(?P<description>.+)$",
    re.MULTILINE,
)

BREAKING_FOOTER_PATTERN = re.compile(
    r"^BREAKING[ -]CHANGE:\s*(.*?)(?=\n\S|\Z)",
    re.MULTILINE | re.IGNORECASE | re.DOTALL,
)

PR_PATTERN = re.compile(r"\(#(\d+)\)")

PRE_RELEASE_PATTERN = re.compile(r"-(?:alpha|beta|rc|dev|preview)\d*", re.IGNORECASE)


def _extract_breaking_footer(raw_message: str) -> str | None:
    lines = raw_message.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^BREAKING[ -]CHANGE:\s*", line, re.IGNORECASE):
            first_content = re.sub(r"^BREAKING[ -]CHANGE:\s*", "", line, flags=re.IGNORECASE)
            footer_lines = [first_content]
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == "":
                    break
                footer_lines.append(lines[j].strip())
            return "\n".join(footer_lines).strip()
    return None


def parse_conventional_commit(raw_message: str) -> ConventionalCommit:
    lines = raw_message.split("\n")
    first_line = lines[0].strip() if lines else ""
    body_lines = [line for line in lines[1:] if not line.startswith("BREAKING") and not line.startswith("Co-authored")]
    body = "\n".join(body_lines).strip()

    match = CONVENTIONAL_PATTERN.match(first_line)
    pr_match = PR_PATTERN.search(first_line)

    if match:
        raw_type = match.group("type").lower()
        commit_type = TYPE_MAP.get(raw_type, CommitType.unknown)
        scope = match.group("scope") or None
        description = match.group("description").strip()
        has_bang = bool(match.group("breaking"))
    else:
        commit_type = CommitType.unknown
        scope = None
        description = first_line
        has_bang = False

    breaking_footer = _extract_breaking_footer(raw_message)
    is_breaking = has_bang or (breaking_footer is not None)

    pr_number = None
    if pr_match:
        try:
            pr_number = int(pr_match.group(1))
        except ValueError:
            pass

    return ConventionalCommit(
        sha="",
        raw_message=raw_message,
        type=commit_type,
        scope=scope,
        description=description,
        body=body,
        is_breaking=is_breaking,
        breaking_footer=breaking_footer,
        author="",
        date="",
        pr_number=pr_number,
    )


def group_by_type(commits: list[ConventionalCommit]) -> dict[CommitType, list[ConventionalCommit]]:
    groups: dict[CommitType, list[ConventionalCommit]] = {}
    for c in commits:
        t = c["type"]
        if t not in groups:
            groups[t] = []
        groups[t].append(c)
    return groups


def detect_tag_convention(tags: list[dict[str, Any]]) -> str:
    if not tags:
        return "unknown"
    recent = tags[:20]
    pre_release_count = sum(
        1 for t in recent
        if PRE_RELEASE_PATTERN.search(t.get("name", t.get("ref", "")))
    )
    if pre_release_count > len(recent) * 0.3:
        return "pre_release"
    return "semver"

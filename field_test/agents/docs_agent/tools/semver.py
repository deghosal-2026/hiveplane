from __future__ import annotations
import logging
import re
from typing import Any

from llm.client import LLMClient
from state import ClassifiedCommit, BreakingChange, SemverProposal

logger = logging.getLogger(__name__)

SEMVER_PATTERN = re.compile(
    r"^(?:[a-zA-Z][a-zA-Z0-9._+-]*-)?v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
    r"(?P<pre>-(?:alpha|beta|rc|dev|preview)(?:\.\d+|\d*)?)?",
    re.IGNORECASE,
)


def parse_version(tag: str) -> dict[str, Any]:
    match = SEMVER_PATTERN.match(tag)
    if not match:
        logger.warning("Could not parse version from tag: %s", tag)
        return {"major": 0, "minor": 0, "patch": 0, "pre_release": None, "valid": False, "has_v_prefix": False, "prefix": ""}
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch")) if match.group("patch") else 0
    pre = match.group("pre")
    prefix_start = match.start("major")
    raw_prefix = tag[:prefix_start]
    has_v = raw_prefix.endswith("v")
    if has_v:
        raw_prefix = raw_prefix[:-1]
    return {
        "major": major,
        "minor": minor,
        "patch": patch,
        "pre_release": pre,
        "valid": True,
        "has_v_prefix": has_v,
        "prefix": raw_prefix,
    }


def _build_propose_prompt(
    current_version: str,
    classified: list[ClassifiedCommit],
    breaking: list[BreakingChange],
    tag_convention: str,
) -> tuple[str, str]:
    feat_count = sum(1 for c in classified if c["commit"]["type"].value == "feat")
    fix_count = sum(1 for c in classified if c["commit"]["type"].value == "fix")
    breaking_count = len(breaking)

    system = (
        "You are a semver versioning expert. Given the current version, classified commits, "
        "breaking changes, and tag convention, propose the next version.\n\n"
        "Rules:\n"
        "- Major bump if any breaking changes exist\n"
        "- Minor bump if new features exist (no breaking changes)\n"
        "- Patch bump if only fixes/refactors/chores/docs\n"
        "- If tag convention is 'pre_release', increment pre-release identifier instead\n\n"
        "Output JSON with fields: "
        "current_version, proposed_version, bump_type, breaking_count, feature_count, fix_count, justification"
    )

    user = (
        f"Current version: {current_version}\n"
        f"Tag convention: {tag_convention}\n"
        f"Breaking changes: {breaking_count}\n"
        f"Features: {feat_count}\n"
        f"Fixes: {fix_count}\n"
        f"Total classified: {len(classified)}\n"
        "Propose the next version following semver rules."
    )

    return system, user


def propose_semver(
    current_version: str,
    classified: list[ClassifiedCommit],
    breaking: list[BreakingChange],
    tag_convention: str,
    llm: LLMClient | None = None,
    head_tag: str | None = None,
) -> SemverProposal:
    feat_count = sum(1 for c in classified if c["commit"]["type"].value == "feat")
    fix_count = sum(1 for c in classified if c["commit"]["type"].value == "fix")
    breaking_count = len(breaking)

    parsed = parse_version(current_version)
    head_parsed = parse_version(head_tag) if head_tag else None

    rules_proposal = _rules_based_semver(parsed, breaking_count, feat_count, fix_count, tag_convention, head_parsed)

    if llm is None:
        llm = LLMClient()

    system, user = _build_propose_prompt(current_version, classified, breaking, tag_convention)

    try:
        result = llm.complete_structured(
            system_prompt=system,
            user_prompt=user,
            max_tokens=1024,
            temperature=0.1,
        )
        return SemverProposal(
            current_version=current_version,
            proposed_version=result.get("proposed_version", rules_proposal["proposed_version"]),
            bump_type=result.get("bump_type", rules_proposal["bump_type"]),
            breaking_count=breaking_count,
            feature_count=feat_count,
            fix_count=fix_count,
            justification=result.get("justification", rules_proposal["justification"]),
        )
    except Exception as e:
        logger.warning("LLM semver proposal failed, using rules-based: %s", e)
        return SemverProposal(
            current_version=current_version,
            proposed_version=rules_proposal["proposed_version"],
            bump_type=rules_proposal["bump_type"],
            breaking_count=breaking_count,
            feature_count=feat_count,
            fix_count=fix_count,
            justification=rules_proposal["justification"],
        )


def _rules_based_semver(
    parsed: dict[str, Any],
    breaking_count: int,
    feat_count: int,
    fix_count: int,
    tag_convention: str,
    head_parsed: dict[str, Any] | None = None,
) -> dict[str, str]:
    if not parsed.get("valid"):
        return {"proposed_version": "0.0.0", "bump_type": "unknown", "justification": "Could not parse current version"}

    major = parsed["major"]
    minor = parsed["minor"]
    patch = parsed["patch"]
    prefix = parsed.get("prefix", "")
    v_prefix = "v" if parsed.get("has_v_prefix", False) else ""
    pre = parsed.get("pre_release")

    is_patch_branch = (
        head_parsed is not None
        and head_parsed.get("valid")
        and head_parsed["major"] == major
        and head_parsed["minor"] == minor
    )

    is_pre_release = pre is not None or tag_convention == "pre_release"

    if is_pre_release and not is_patch_branch:
        if pre:
            pre_match = re.match(r"-(?:alpha|beta|rc|dev|preview)(?:\.)?(\d+)?", pre, re.IGNORECASE)
            label_match = re.match(r"-(\w+)", pre, re.IGNORECASE)
            pre_label = label_match.group(1) if label_match else "rc"
            pre_num = int(pre_match.group(1)) + 1 if pre_match and pre_match.group(1) else 1
            proposed = f"{prefix}{v_prefix}{major}.{minor}.{patch}-{pre_label}.{pre_num}"
        else:
            proposed = f"{prefix}{v_prefix}{major}.{minor}.{patch}-rc.1"
        bump = "prerelease"
        justification = f"Pre-release bump ({feat_count} features, {fix_count} fixes, {breaking_count} breaking)"
    elif breaking_count > 0 and not is_patch_branch:
        major += 1
        minor = 0
        patch = 0
        proposed = f"{prefix}{v_prefix}{major}.{minor}.{patch}"
        bump = "major"
        justification = f"{breaking_count} breaking change(s) — requires major version bump"
    elif feat_count > 0 and not is_patch_branch:
        minor += 1
        patch = 0
        proposed = f"{prefix}{v_prefix}{major}.{minor}.{patch}"
        bump = "minor"
        justification = f"{feat_count} new feature(s) — minor version bump"
    else:
        patch += 1
        proposed = f"{prefix}{v_prefix}{major}.{minor}.{patch}"
        bump = "patch"
        if is_patch_branch:
            justification = f"Patch-branch release ({prefix}{v_prefix}{major}.{minor}.x): forcing patch bump despite {feat_count} cherry-picked feature(s) and {breaking_count} breaking change(s)"
        else:
            justification = f"{fix_count} fix(es) — patch version bump"

    if tag_convention == "pre_release" and bump != "prerelease" and not is_patch_branch:
        proposed = f"{prefix}{v_prefix}{major}.{minor}.{patch}-rc.1"

    return {"proposed_version": proposed, "bump_type": bump, "justification": justification}

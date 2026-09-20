"""Read-only enforcement for pack rules."""

from __future__ import annotations

from pathlib import Path

from cauterule.models.rule import StandingRule
from cauterule.store.manager import resolve_inside, validate_rule_id


def check_readonly(rule: StandingRule, base_dir: str = "rules") -> bool:
    """Check whether a rule belongs to an installed pack and is readonly.

    A rule is considered readonly when its ``pack`` field is set *and* the
    corresponding pack directory exists under ``<base_dir>/packs/``.

    Args:
        rule: The rule to check.
        base_dir: Root directory for rule packs.

    Returns:
        ``True`` if the rule is a pack rule (readonly), ``False`` otherwise.
    """
    if rule.pack is None:
        return False
    try:
        validate_rule_id(rule.pack)
        pack_dir = resolve_inside(Path(base_dir) / "packs", rule.pack)
    except ValueError:
        return False
    return pack_dir.is_dir() and (
        (pack_dir / "pack.yaml").is_file() or (pack_dir / "manifest.yaml").is_file()
    )

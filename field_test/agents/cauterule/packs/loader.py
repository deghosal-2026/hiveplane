"""Pack loader — discovers and loads pack rules from disk."""

from __future__ import annotations

from pathlib import Path

import yaml

from cauterule.models.rule import StandingRule
from cauterule.packs.format import PackManifest
from cauterule.serialization.rule_yaml import load_rule_from_file
from cauterule.store.manager import resolve_inside, validate_rule_id


def load_pack(
    name: str,
    base_dir: str = "rules",
) -> tuple[PackManifest, list[StandingRule]]:
    """Load a rule pack by name.

    Expected layout::

        <base_dir>/packs/<name>/manifest.yaml
        <base_dir>/packs/<name>/R-*.yaml

    Args:
        name: Pack name (subdirectory under ``base_dir/packs/``).
        base_dir: Root directory for rule packs.

    Returns:
        A ``(manifest, rules)`` tuple.

    Raises:
        FileNotFoundError: If the pack directory or manifest does not exist.
        ValueError: If the manifest YAML is invalid, rule files are missing,
            or *name* / rule ids are unsafe for paths (#499).
    """
    validate_rule_id(name)
    packs_root = Path(base_dir) / "packs"
    pack_dir = resolve_inside(packs_root, name)
    # pack.yaml preferred; legacy manifest.yaml synthesised on install (#554).
    manifest_path = pack_dir / "pack.yaml"
    if not manifest_path.is_file():
        manifest_path = pack_dir / "manifest.yaml"

    if not pack_dir.is_dir():
        raise FileNotFoundError(f"Pack directory not found: {pack_dir}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Pack manifest not found: {manifest_path}")

    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Pack manifest must be a mapping, got {type(raw).__name__}")

    manifest = PackManifest.from_dict(raw)

    rules: list[StandingRule] = []
    missing: list[str] = []
    for rule_id in manifest.rules:
        validate_rule_id(rule_id)
        rule_path = resolve_inside(pack_dir, f"{rule_id}.yaml")
        if not rule_path.is_file():
            # Tolerate the rules/ subdirectory layout from pack create.
            rule_path = resolve_inside(pack_dir, "rules", f"{rule_id}.yaml")
        if not rule_path.is_file():
            missing.append(rule_id)
            continue
        rules.append(load_rule_from_file(rule_path))

    if missing:
        raise ValueError(f"Pack {name!r} is missing rule files: {', '.join(missing)}")

    return manifest, rules

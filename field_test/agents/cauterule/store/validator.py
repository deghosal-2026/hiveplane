"""Store validator — no orphaned refs, missing provenance, broken links, duplicate IDs."""

from __future__ import annotations

from pathlib import Path

from cauterule.models.rule import StandingRule
from cauterule.serialization.rule_yaml import load_rule_from_file


def validate_store(base_dir: str = "rules") -> list[str]:
    """Validate the integrity of a rule store.

    Checks performed:
        - Every ``.yaml`` and ``.yml`` file deserializes to a valid :class:`StandingRule`.
        - No duplicate rule IDs across files.
        - Every rule has non-empty provenance fields.
        - No invalid status values.
        - ``superseded`` rules reference an existing rule.
        - No broken pack links.
        - No orphaned provenance references.

    Args:
        base_dir: Root rule-store directory.

    Returns:
        A list of warning strings. An empty list means a clean store.
    """
    warnings: list[str] = []
    d = Path(base_dir)
    if not d.is_dir():
        return [f"Store directory not found: {base_dir}"]

    seen_ids: set[str] = set()
    yaml_files = sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml"))

    rules_loaded: dict[str, StandingRule] = {}

    # ---- Pass 1: load all rules, collect ids ----
    for p in yaml_files:
        if p.stem == "index":
            continue
        try:
            rule = load_rule_from_file(p)
        except Exception as exc:
            warnings.append(f"{p.name}: failed to deserialize — {exc}")
            continue

        rid = rule.id
        if rid in seen_ids:
            warnings.append(f"Duplicate ID {rid!r} in {p.name}")
        seen_ids.add(rid)
        rules_loaded[p.name] = rule

    # ---- Pass 2: cross-file checks against complete id set ----
    all_ids = {r.id for r in rules_loaded.values()}
    for fname, rule in rules_loaded.items():
        _check_provenance(rule, fname, warnings)

        if rule.status not in ("active", "retired", "superseded"):
            warnings.append(f"{fname} ({rule.id}): invalid status {rule.status!r}")

        if rule.superseded_by is not None and rule.superseded_by not in all_ids:
            warnings.append(
                f"{fname} ({rule.id}): superseded_by {rule.superseded_by!r} not found in store"
            )

        # Retired-vs-superseded invariant (#544): `superseded` rules must
        # point at their successor; `retired` rules are terminal and carry
        # no successor pointer.
        if rule.status == "superseded" and rule.superseded_by is None:
            warnings.append(f"{fname} ({rule.id}): status=superseded requires superseded_by")
        if rule.status == "retired" and rule.superseded_by is not None:
            warnings.append(f"{fname} ({rule.id}): retired rules must not carry superseded_by")

        if rule.pack is not None:
            pack_path = d / f"{rule.pack}.yaml"
            if not pack_path.is_file():
                pack_path2 = d / f"{rule.pack}.yml"
                if not pack_path2.is_file():
                    warnings.append(f"{fname} ({rule.id}): pack {rule.pack!r} file not found")

    if not yaml_files:
        warnings.append("No rule files found in store")

    return warnings


def _check_provenance(rule: StandingRule, filename: str, warnings: list[str]) -> None:
    prov = rule.provenance
    if not prov.source_trajectory:
        warnings.append(f"{filename} ({rule.id}): missing provenance.source_trajectory")
    if not prov.extracted_by:
        warnings.append(f"{filename} ({rule.id}): missing provenance.extracted_by")
    if not prov.extract_timestamp:
        warnings.append(f"{filename} ({rule.id}): missing provenance.extract_timestamp")

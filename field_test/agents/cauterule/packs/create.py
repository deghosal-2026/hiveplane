"""Pack scaffolding — ``cauterule pack create`` (#555).

Builds a shippable pack directory (``pack.yaml`` + flat ``R-*.yaml`` rules
+ ``README.md`` + replay fixtures) out of rules already in the store.
"""

from __future__ import annotations

import difflib
import getpass
import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from cauterule import __version__
from cauterule.models.rule import StandingRule
from cauterule.packs.certification import certify_pack
from cauterule.packs.format import PackManifest
from cauterule.store.manager import StoreManager

PACK_NAME_RE = re.compile(r"^[a-z0-9-]+$")
LICENSE_STUBS = {
    "MIT": (
        "MIT License\n\nCopyright (c) {author}\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        'of this software and associated documentation files (the "Software"), to deal\n'
        "in the Software without restriction, including without limitation the rights\n"
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
        "copies of the Software, and to permit persons to whom the Software is\n"
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in all\n"
        "copies or substantial portions of the Software.\n"
    ),
}


def _default_author() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return getpass.getuser()
    except OSError:
        return "Cauterule"


def _verbatim_rule_bytes(rule: StandingRule, store_dir: Path) -> bytes:
    """Return the exact stored bytes for *rule* (never rewritten)."""
    candidates = [
        store_dir / f"{rule.id}.yaml",
        store_dir / f"{rule.id}.yml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_bytes()
    # Fallback: re-serialize (should not normally happen).
    return yaml.safe_dump(rule.to_dict(), sort_keys=False).encode()


def select_rules(
    store: str = "rules",
    from_tags: tuple[str, ...] = (),
    from_taxonomies: tuple[str, ...] = (),
    rule_ids: tuple[str, ...] = (),
    all_promoted: bool = False,
    include_candidate: bool = False,
) -> tuple[list[StandingRule], list[str]]:
    """Select store rules for a new pack.

    Returns (selected, notes). Only ``active`` rules owned by the store are
    eligible by default (installed pack rules are excluded); ``include_candidate``
    additionally admits non-active rules with a warning note.
    """
    manager = StoreManager(base_dir=store)
    owned = [r for r in manager.list_rules() if r.pack is None]
    eligible = owned if not include_candidate else manager.list_rules()
    notes: list[str] = []
    if include_candidate:
        notes.append("warning: --include-candidate admits non-active rules")

    if not (from_tags or from_taxonomies or rule_ids or all_promoted):
        tried = "no selectors given (need --from-tag, --from-taxonomy, --rule, or --all-promoted)"
        raise ValueError(f"empty selection: {tried}")

    selected: dict[str, StandingRule] = {}
    by_id = {r.id: r for r in eligible}
    dupes = 0

    def _add(rule: StandingRule) -> None:
        nonlocal dupes
        if rule.id in selected:
            dupes += 1
        selected[rule.id] = rule

    if all_promoted:
        for rule in eligible:
            if include_candidate or rule.status == "active":
                _add(rule)
    for tag in from_tags:
        matched = [r for r in eligible if tag in (r.tags or ())]
        if not matched:
            known = sorted({t for r in eligible for t in (r.tags or ())})
            hint = difflib.get_close_matches(tag, known, n=3)
            suffix = f" — did you mean {hint}?" if hint else ""
            raise ValueError(f"no rules tagged {tag!r}{suffix}")
        for rule in matched:
            _add(rule)
    for taxonomy in from_taxonomies:
        matched = [r for r in eligible if (r.taxonomy or "") == taxonomy]
        if not matched:
            known = sorted({r.taxonomy for r in eligible if r.taxonomy})
            hint = difflib.get_close_matches(taxonomy, known, n=3)
            suffix = f" — did you mean {hint}?" if hint else ""
            raise ValueError(f"no rules under taxonomy {taxonomy!r}{suffix}")
        for rule in matched:
            _add(rule)
    for rule_id in rule_ids:
        rule_obj = by_id.get(rule_id)
        if rule_obj is None:
            hint = difflib.get_close_matches(rule_id, list(by_id), n=3)
            suffix = f" — did you mean {hint}?" if hint else ""
            raise ValueError(f"rule {rule_id!r} not found in store{suffix}")
        _add(rule_obj)

    if dupes:
        notes.append(f"deduped {dupes} overlapping selection(s)")
    return sorted(selected.values(), key=lambda r: r.id), notes


def create_pack(
    name: str,
    store: str = "rules",
    from_tag: tuple[str, ...] = (),
    from_taxonomy: tuple[str, ...] = (),
    rule: tuple[str, ...] = (),
    all_promoted: bool = False,
    include_candidate: bool = False,
    version: str = "0.1.0",
    description: str = "",
    author: str | None = None,
    license: str = "MIT",
    out: str | None = None,
    run_cert: bool = True,
    min_rules: int = 1,
    force: bool = False,
) -> dict[str, Any]:
    """Scaffold a pack directory from store rules.

    Returns a summary dict. Raises ValueError with an actionable message.
    """
    if not PACK_NAME_RE.match(name):
        raise ValueError(
            f"invalid pack name {name!r}: use lowercase letters, digits, hyphens (e.g. pack-docker)"
        )
    resolved_author = author or _default_author()
    resolved_out = Path(out or name)
    if resolved_out.exists() and any(resolved_out.iterdir()) and not force:
        raise ValueError(f"refusing to write into non-empty {resolved_out}: use --force")

    selected, notes = select_rules(
        store=store,
        from_tags=from_tag,
        from_taxonomies=from_taxonomy,
        rule_ids=rule,
        all_promoted=all_promoted,
        include_candidate=include_candidate,
    )
    if len(selected) < min_rules:
        raise ValueError(
            f"selection has {len(selected)} rule(s), fewer than --min-rules {min_rules}"
        )

    store_dir = Path(store)
    out_dir = resolved_out
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tests_dir = out_dir / "tests"
    out_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    # Flat layout (pack.yaml + R-*.yaml at root) matches pack-git and the
    # loader; manifests list the rule ids.
    for rule_obj in selected:
        (out_dir / f"{rule_obj.id}.yaml").write_bytes(_verbatim_rule_bytes(rule_obj, store_dir))

    manifest = PackManifest(
        name=name,
        version=version,
        description=description or f"Rules for {name}",
        author=resolved_author,
        rules=tuple(r.id for r in selected),
        license=license,
    )
    pack_yaml = {
        **manifest.to_dict(),
        "created_with": f"cauterule {__version__}",
        "cert": {"required": True, "min_score": 0.8},
        "marketplace": {"categories": [], "keywords": []},
    }
    (out_dir / "pack.yaml").write_text(yaml.safe_dump(pack_yaml, sort_keys=False), encoding="utf-8")
    # Legacy-compat copy until the loader migrates fully to pack.yaml.
    (out_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest.to_dict(), sort_keys=False), encoding="utf-8"
    )

    fixtures = []
    for rule_obj in selected:
        fixtures.append(
            {
                "rule_id": rule_obj.id,
                "pack": name,
                "todo": (
                    "TODO: replace with a real failure trajectory "
                    f"for {rule_obj.id} (trigger: {rule_obj.when.trigger[:80]})"
                ),
                "expected_outcome": {"verdict": "reproducible", "rule_id": rule_obj.id},
            }
        )
    (tests_dir / f"replay_{name}.jsonl").write_text(
        "\n".join(json.dumps(f) for f in fixtures) + "\n", encoding="utf-8"
    )
    (tests_dir / "conftest.py").write_text(
        f'"""Load pack {name} from this directory."""\n',
        encoding="utf-8",
    )

    rows = "\n".join(
        f"| {r.id} | {r.when.trigger[:60]} | {r.do.directive[:60]} | TODO |" for r in selected
    )
    (out_dir / "README.md").write_text(
        f"# {name}\n\n> {manifest.description}\n\n"
        f"`cauterule pack install <repo>#{name}@{version}`\n\n"
        "## Rules\n\n"
        "| Rule ID | Trigger | Fix summary | Fixtures |\n"
        "|---|---|---|---|\n"
        f"{rows}\n\n"
        "## Certification\n\n"
        f"Run `cauterule test --pack {name}` to re-certify.\n",
        encoding="utf-8",
    )
    (out_dir / "LICENSE").write_text(
        LICENSE_STUBS.get(license, LICENSE_STUBS["MIT"]).format(author=resolved_author),
        encoding="utf-8",
    )
    (out_dir / ".gitignore").write_text("cache/\n*.pyc\n__pycache__/\n", encoding="utf-8")

    cert_report: dict[str, Any] = {"passed": True, "checks": [], "skipped": True}
    if run_cert:
        cert_report = certify_pack(
            {"manifest": manifest, "rules": [{"id": r.id} for r in selected]}
        )
        if not cert_report.get("passed", False):
            failing = "; ".join(
                f"{c['name']}: {c['message']}"
                for c in cert_report["checks"]
                if c["status"] != "pass"
            )
            raise ValueError(
                f"created {out_dir} but certify_pack() failed: {failing} "
                "(directory left in place for fixing; re-run with --no-cert to skip)"
            )

    return {
        "name": name,
        "version": version,
        "rules": [r.id for r in selected],
        "path": str(out_dir),
        "cert": cert_report,
        "notes": notes,
    }

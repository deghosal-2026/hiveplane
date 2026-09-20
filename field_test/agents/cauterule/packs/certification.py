"""Pack certification — safety, replay, and provenance checks for rule packs."""

from __future__ import annotations

from typing import Any

from cauterule.packs.format import PackManifest, validate_manifest


def certify_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Run certification checks on a rule pack.

    Checks performed:

    1. **Safety** — manifest fields are non-empty and valid.
    2. **Replay** — the pack contains at least one rule.
    3. **Provenance** — all rules have a non-empty ``id`` field.

    Args:
        pack: A dict with keys ``"manifest"`` (a :class:`PackManifest`
            or compatible dict) and ``"rules"`` (list of rule dicts).

    Returns:
        A dict with ``"passed"`` (bool) and ``"checks"`` (list of check
        result dicts, each with ``"name"``, ``"status"``, and
        ``"message"``).
    """
    manifest_raw = pack.get("manifest", {})
    rules: list[dict[str, Any]] = pack.get("rules", [])

    if isinstance(manifest_raw, dict):
        manifest = PackManifest.from_dict(manifest_raw)
    else:
        manifest = manifest_raw  # already a PackManifest instance

    checks: list[dict[str, Any]] = []

    # --- Safety check ---
    safety_errors = (
        validate_manifest(manifest)
        if isinstance(manifest, PackManifest)
        else ["Invalid manifest type"]
    )
    if safety_errors:
        checks.append(
            {
                "name": "safety",
                "status": "fail",
                "message": "; ".join(safety_errors),
            }
        )
    else:
        checks.append(
            {
                "name": "safety",
                "status": "pass",
                "message": "Manifest fields are valid.",
            }
        )

    # --- Replay check ---
    if len(rules) == 0:
        checks.append(
            {
                "name": "replay",
                "status": "fail",
                "message": "Pack contains no rules.",
            }
        )
    else:
        checks.append(
            {
                "name": "replay",
                "status": "pass",
                "message": f"Pack contains {len(rules)} rule(s).",
            }
        )

    # --- Provenance check ---
    missing_ids = [i for i, r in enumerate(rules) if not r.get("id")]
    if missing_ids:
        checks.append(
            {
                "name": "provenance",
                "status": "fail",
                "message": f"Rules at indices {missing_ids} are missing an 'id' field.",
            }
        )
    else:
        checks.append(
            {
                "name": "provenance",
                "status": "pass",
                "message": "All rules have an 'id' field.",
            }
        )

    passed = all(c["status"] == "pass" for c in checks)
    return {"passed": passed, "checks": checks}

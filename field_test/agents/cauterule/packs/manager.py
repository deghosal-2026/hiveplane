"""Pack manager — discover and inspect installed rule packs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from cauterule.packs.format import PackManifest
from cauterule.store.manager import resolve_inside, validate_rule_id


def list_packs(base_dir: str = "rules") -> list[str]:
    """List available pack names under ``<base_dir>/packs/``.

    A directory is considered a pack if it contains a ``pack.yaml`` or
    legacy ``manifest.yaml`` file.

    Args:
        base_dir: Root directory for rule packs.

    Returns:
        Sorted list of pack names.
    """
    packs_root = Path(base_dir) / "packs"
    if not packs_root.is_dir():
        return []

    names: list[str] = sorted(
        p.name
        for p in packs_root.iterdir()
        if p.is_dir() and ((p / "pack.yaml").is_file() or (p / "manifest.yaml").is_file())
    )
    return names


def pack_info(name: str, base_dir: str = "rules") -> dict[str, Any]:
    """Return metadata for a named pack as a plain dict.

    Args:
        name: Pack name.
        base_dir: Root directory for rule packs.

    Returns:
        A dict with manifest fields plus a ``rule_count`` key.

    Raises:
        FileNotFoundError: If the pack does not exist.
        ValueError: If *name* is unsafe for paths (#499).
    """
    validate_rule_id(name)
    pack_dir = resolve_inside(Path(base_dir) / "packs", name)
    manifest_path = pack_dir / "pack.yaml"
    if not manifest_path.is_file():
        manifest_path = pack_dir / "manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Pack manifest not found: {pack_dir}")

    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest = PackManifest.from_dict(raw)
    info = manifest.to_dict()
    info["rule_count"] = len(manifest.rules)
    install_path = pack_dir / "INSTALL.json"
    if install_path.is_file():
        try:
            install_record = json.loads(install_path.read_text(encoding="utf-8"))
            if isinstance(install_record, dict):
                info["cert"] = install_record.get("cert")
                info["safety"] = install_record.get("safety")
                info["source"] = install_record.get("source")
                info["installed_at"] = install_record.get("installed_at")
        except (OSError, ValueError):
            pass
    return info

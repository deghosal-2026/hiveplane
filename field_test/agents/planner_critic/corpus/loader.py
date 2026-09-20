"""Corpus infrastructure — loader, validator, checksum, and CLI helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .types import CorpusManifest, SecurityInstance


def _compute_sha256(data: dict[str, Any]) -> str:
    """Compute the SHA-256 hex digest of a sorted JSON-serialised dict."""
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_corpus_manifest(corpus_dir: str | Path) -> CorpusManifest | None:
    """Load the manifest file from a corpus directory.

    Args:
        corpus_dir: Path to the corpus directory containing ``manifest.json``.

    Returns:
        The parsed manifest, or ``None`` when the file does not exist.
    """
    path = Path(corpus_dir) / "manifest.json"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)
    return CorpusManifest.model_validate(data)


def load_all_instances(
    corpus_dir: str | Path,
    *,
    verify_checksums: bool = False,
) -> list[SecurityInstance]:
    """Load every instance from a corpus directory.

    Args:
        corpus_dir: Path to the corpus root (containing ``manifest.json`` and
            ``instances/``).
        verify_checksums: When True, compute and verify SHA-256 checksums
            against the manifest. Skips instances that fail verification.

    Returns:
        A list of loaded :class:`SecurityInstance` objects.
    """
    manifest = load_corpus_manifest(corpus_dir)
    instances: list[SecurityInstance] = []

    base = Path(corpus_dir) / "instances"
    if not base.exists():
        return instances

    for path in sorted(base.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(raw)
        instance = SecurityInstance.model_validate(data)

        if verify_checksums and manifest is not None:
            expected = manifest.instances.get(instance.instance_id)
            if expected is not None:
                actual = _compute_sha256(data)
                if actual != expected:
                    continue

        instances.append(instance)

    return instances


def load_instance(corpus_dir: str | Path, instance_id: str) -> SecurityInstance | None:
    """Load a single instance by ID from a corpus directory.

    Args:
        corpus_dir: Path to the corpus root.
        instance_id: The instance identifier (matches the filename stem).

    Returns:
        The parsed instance, or ``None`` when the file does not exist.
    """
    path = Path(corpus_dir) / "instances" / f"{instance_id}.json"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)
    return SecurityInstance.model_validate(data)


def list_instances(corpus_dir: str | Path) -> list[dict[str, object]]:
    """List summary metadata for every instance in a corpus.

    Args:
        corpus_dir: Path to the corpus root.

    Returns:
        A list of dicts with ``instance_id``, ``cwe``, ``cwe_bucket``,
        ``vulnerability_class``, and ``expected_critic_signal``.
    """
    instances = load_all_instances(corpus_dir)
    return [
        {
            "instance_id": inst.instance_id,
            "cwe": inst.cwe,
            "cwe_bucket": inst.cwe_bucket.value,
            "vulnerability_class": inst.vulnerability_class,
            "expected_critic_signal": (
                inst.expected_critic_signal.value if inst.expected_critic_signal else None
            ),
        }
        for inst in instances
    ]


__all__ = [
    "list_instances",
    "load_all_instances",
    "load_corpus_manifest",
    "load_instance",
]

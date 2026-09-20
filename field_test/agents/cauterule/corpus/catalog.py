"""Catalog loader — parse catalog.yaml into CorpusMetadata objects."""

from __future__ import annotations

from pathlib import Path

import yaml

from cauterule.corpus.format import CorpusMetadata


def load_catalog(path: str | Path) -> dict[str, CorpusMetadata]:
    """Parse a ``catalog.yaml`` into a mapping of corpus id → metadata.

    Args:
        path: Path to ``catalog.yaml``.

    Returns:
        Dict of ``{corpus_id: CorpusMetadata}``.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML is invalid or entries are malformed.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Catalog not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004
            f"Catalog must be a mapping, got {type(raw).__name__}"
        )
    catalog: dict[str, CorpusMetadata] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            raise TypeError(f"Catalog entry {key!r} must be a mapping")
        meta = CorpusMetadata.from_dict(val)
        catalog[meta.id or key] = meta
    return catalog

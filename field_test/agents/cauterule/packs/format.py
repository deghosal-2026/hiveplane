"""Pack format specification and validation utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PackManifest:
    """Metadata for a versioned rule pack."""

    name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    rules: tuple[str, ...] = field(default_factory=tuple)
    license: str = ""
    deps: tuple[str, ...] = field(default_factory=tuple)
    cauterule: str = ""
    checksum: str = ""
    marketplace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        pass

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "rules": list(self.rules),
            "license": self.license,
            "deps": list(self.deps),
            "cauterule": self.cauterule,
            "checksum": self.checksum,
            "marketplace": dict(self.marketplace),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackManifest:
        """Create from a dict produced by :meth:`to_dict`."""
        raw_deps = data.get("deps", [])
        if isinstance(raw_deps, list):
            deps = tuple(str(d) for d in raw_deps)
        elif isinstance(raw_deps, str):
            deps = (raw_deps,)
        else:
            deps = ()
        raw_marketplace = data.get("marketplace", {})
        marketplace = dict(raw_marketplace) if isinstance(raw_marketplace, dict) else {}
        return cls(
            name=data.get("name", ""),
            version=data.get("version", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            rules=tuple(data.get("rules", [])),
            license=data.get("license", ""),
            deps=deps,
            cauterule=data.get("cauterule", ""),
            checksum=data.get("checksum", ""),
            marketplace=marketplace,
        )


def create_manifest(**kwargs: Any) -> PackManifest:
    """Create a :class:`PackManifest` from keyword arguments.

    Args:
        **kwargs: Passed directly to ``PackManifest(**kwargs)``.

    Returns:
        A new :class:`PackManifest` instance.
    """
    return PackManifest(**kwargs)


def validate_manifest(manifest: PackManifest) -> list[str]:
    """Validate a pack manifest, returning a list of error messages.

    An empty list means the manifest is valid.

    Args:
        manifest: The manifest to validate.

    Returns:
        List of human-readable error strings.
    """
    errors: list[str] = []
    if not manifest.name or not manifest.name.strip():
        errors.append("name must be non-blank")
    if not manifest.version or not manifest.version.strip():
        errors.append("version must be non-blank")
    if not manifest.description or not manifest.description.strip():
        errors.append("description must be non-blank")
    if not manifest.author or not manifest.author.strip():
        errors.append("author must be non-blank")
    if not manifest.rules:
        errors.append("rules must contain at least one rule ID")
    else:
        for i, r in enumerate(manifest.rules):
            if not r or not r.strip():
                errors.append(f"rules[{i}] must be non-blank")
    return errors

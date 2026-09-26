"""Desired-state loading: directory and git sources (M26-01, D22).

The loader turns a fleet manifest set into a validated, atomic
:class:`~hiveplane.reconcile.models.DesiredSet`. Every document is validated
before any of it is returned, so a bad revision is rejected whole with nothing
applied. Loading is strictly read-only: the git source is fetched into a
temporary worktree and the source repository is never written to.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hiveplane.core.manifest import parse_manifest
from hiveplane.fleet.reconcile import DesiredSpec, SpecKind, SpecSource
from hiveplane.policy.models import PolicyPack
from hiveplane.reconcile.models import DesiredSet
from hiveplane.tenancy.context import DEFAULT_TENANT_ID

#: Manifest document kinds accepted by the loader, mapped to the fleet spec kind.
_KIND_ALIASES: dict[str, SpecKind] = {
    "workload": SpecKind.WORKLOAD,
    "agentworkload": SpecKind.WORKLOAD,
    "policy": SpecKind.POLICY,
    "policy_pack": SpecKind.POLICY,
    "policypack": SpecKind.POLICY,
    "trigger": SpecKind.TRIGGER,
    "budget": SpecKind.BUDGET,
}


class LoaderError(Exception):
    """Raised when a desired-state source cannot be loaded or validated."""


class GitSource(BaseModel):
    """A read-only git source of desired state."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000)
    path: str = Field(default=".", max_length=2000)
    ref: str = Field(default="HEAD", min_length=1, max_length=253)
    auth_secret_ref: str | None = Field(default=None, max_length=253)


def authenticated_url(source: GitSource, token: str) -> str:
    """Return ``source.url`` with an HTTPS access token injected for auth.

    Git credentials are resolved from a secret reference, never inlined in the
    desired-state tree; this helper applies the resolved token only in memory.
    """
    parts = urlsplit(source.url)
    if parts.scheme not in ("http", "https"):
        return source.url
    netloc = f"x-access-token:{token}@{parts.hostname}"
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class DesiredStateLoader:
    """Loads and validates a desired-state set from a directory or git source."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def load_directory(
        self,
        path: str | Path,
        *,
        source_id: str,
        source: SpecSource = SpecSource.GIT,
        revision: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> DesiredSet:
        """Load every ``*.yaml``/``*.yml`` document under ``path`` atomically."""
        root = Path(path)
        if not root.is_dir():
            raise LoaderError(f"desired-state path is not a directory: {root}")

        specs: list[DesiredSpec] = []
        seen: set[tuple[SpecKind, str]] = set()
        for file in sorted((*root.rglob("*.yaml"), *root.rglob("*.yml"))):
            for index, document in enumerate(_read_documents(file)):
                spec = self._to_spec(
                    document,
                    source_id=source_id,
                    source=source,
                    revision=revision or "unresolved",
                    tenant_id=tenant_id,
                    location=f"{file}:{index}",
                )
                key = (spec.kind, spec.name)
                if key in seen:
                    raise LoaderError(
                        f"{file}: duplicate {spec.kind.value} {spec.name!r} in desired set"
                    )
                seen.add(key)
                specs.append(spec)

        resolved_revision = revision or _digest(specs)
        updated = [
            spec.model_copy(update={"revision": resolved_revision}) for spec in specs
        ]
        return DesiredSet(
            source_id=source_id,
            source=source,
            revision=resolved_revision,
            specs=updated,
            generated_at=self._clock(),
        )

    def load_git(
        self,
        source: GitSource,
        *,
        source_id: str,
        secret_resolver: Callable[[str], str] | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> DesiredSet:
        """Fetch ``source`` at its pinned ref and load the tree, read-only."""
        url = source.url
        if source.auth_secret_ref is not None:
            if secret_resolver is None:
                raise LoaderError(
                    f"source {source_id!r} requires secret "
                    f"{source.auth_secret_ref!r} but no resolver was provided"
                )
            url = authenticated_url(source, secret_resolver(source.auth_secret_ref))

        with tempfile.TemporaryDirectory(prefix="hiveplane-reconcile-") as tmp:
            checkout = Path(tmp) / "checkout"
            self._run(["git", "clone", "--quiet", url, str(checkout)], source_id)
            if source.ref not in ("HEAD", "head"):
                self._run(
                    ["git", "-C", str(checkout), "checkout", "--quiet", source.ref],
                    source_id,
                )
            revision = self._run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], source_id
            ).strip()
            subtree = checkout / source.path if source.path not in (".", "") else checkout
            return self.load_directory(
                subtree,
                source_id=source_id,
                source=SpecSource.GIT,
                revision=revision,
                tenant_id=tenant_id,
            )

    @staticmethod
    def _run(command: list[str], source_id: str) -> str:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise LoaderError(f"git source {source_id!r} failed: {detail.strip()}") from exc
        return result.stdout

    def _to_spec(
        self,
        document: Mapping[str, Any],
        *,
        source_id: str,
        source: SpecSource,
        revision: str,
        tenant_id: str,
        location: str,
    ) -> DesiredSpec:
        raw_kind = document.get("kind")
        if not isinstance(raw_kind, str) or raw_kind.lower() not in _KIND_ALIASES:
            raise LoaderError(f"{location}: unknown document kind {raw_kind!r}")
        kind = _KIND_ALIASES[raw_kind.lower()]
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping) or not metadata.get("name"):
            raise LoaderError(f"{location}: document requires metadata.name")
        name = str(metadata["name"])

        try:
            payload = self._validate(kind, document, location)
        except ValidationError as exc:
            raise LoaderError(f"{location}: invalid {kind.value}: {exc}") from exc

        return DesiredSpec(
            spec_id=f"{kind.value}/{name}",
            tenant_id=tenant_id,
            source_id=source_id,
            source=source,
            kind=kind,
            name=name,
            revision=revision,
            content_hash=_hash(payload),
            spec=payload,
            updated_at=self._clock(),
        )

    @staticmethod
    def _validate(
        kind: SpecKind, document: Mapping[str, Any], location: str
    ) -> dict[str, Any]:
        spec = document.get("spec")
        if not isinstance(spec, Mapping):
            raise LoaderError(f"{location}: document requires a spec mapping")
        metadata = dict(document["metadata"])

        if kind is SpecKind.WORKLOAD:
            metadata.setdefault("owner", metadata.get("team") or "platform")
            manifest = {
                "apiVersion": "hiveplane/v1",
                "kind": "AgentWorkload",
                "metadata": metadata,
                "spec": dict(spec),
            }
            return parse_manifest(manifest).model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        if kind is SpecKind.POLICY:
            pack = {
                "apiVersion": "hiveplane/v1",
                "kind": "PolicyPack",
                "metadata": metadata,
                "spec": dict(spec),
            }
            return PolicyPack.model_validate(pack).model_dump(
                by_alias=True, mode="json", exclude_none=True
            )
        # Triggers and budgets are validated structurally until their services
        # land (M27/M49); their declared content is still loaded, hashed, and
        # diffed so drift is visible from day one.
        return {
            "kind": kind.value,
            "metadata": metadata,
            "spec": dict(spec),
        }


def _read_documents(file: Path) -> Iterator[Mapping[str, Any]]:
    try:
        documents = list(yaml.safe_load_all(file.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise LoaderError(f"{file}: failed to parse YAML: {exc}") from exc
    for document in documents:
        if document is None:
            continue
        if not isinstance(document, Mapping):
            raise LoaderError(f"{file}: every document must be a mapping")
        yield document


def _hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _digest(specs: list[DesiredSpec]) -> str:
    material = "|".join(
        sorted(f"{spec.kind.value}:{spec.name}:{spec.content_hash}" for spec in specs)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

"""Pack SPEC parsing for ``cauterule pack install`` (#554).

SPEC forms::

    <owner>/<repo>              # latest release, e.g. acme/pack-docker
    <owner>/<repo>@<version>    # pinned, e.g. acme/pack-docker@v1.2.0
    <name>                      # shorthand resolved via registry
    <name>@<version>            # e.g. pack-docker@1.2.0
    <gist-url>                  # https://gist.github.com/<user>/<id>, gist:<id>
    <local-path>                # local dir or .tar.gz (tests + air-gapped seeding)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PackSpec:
    """Parsed install SPEC."""

    kind: str  # "github" | "shorthand" | "gist" | "local"
    name: str = ""  # shorthand name (kind == "shorthand")
    owner: str = ""  # github owner (kind == "github")
    repo: str = ""  # github repo (kind == "github")
    version: str = ""  # pinned version, "" means latest
    gist_id: str = ""  # gist id (kind == "gist")
    path: str = ""  # local path (kind == "local")


def parse_spec(spec: str) -> PackSpec:
    """Parse an install SPEC string into a :class:`PackSpec`.

    Raises:
        ValueError: If the SPEC is blank or malformed.
    """
    raw = spec.strip()
    if not raw:
        raise ValueError("SPEC must be non-blank")

    # Gist URL forms (per #547 contract).
    if raw.startswith("gist:"):
        gist_id = raw[len("gist:") :].strip()
        if not gist_id:
            raise ValueError(f"invalid gist SPEC {spec!r}: missing id after 'gist:'")
        return PackSpec(kind="gist", gist_id=gist_id)
    if "gist.github.com" in raw:
        gist_id = raw.rstrip("/").rsplit("/", 1)[-1].strip()
        if not gist_id:
            raise ValueError(f"invalid gist SPEC {spec!r}: missing id")
        return PackSpec(kind="gist", gist_id=gist_id)

    # Local path (existing dir or .tar.gz file).
    candidate = Path(raw)
    if candidate.exists() and (candidate.is_dir() or candidate.is_file()):
        return PackSpec(kind="local", path=raw)
    if raw.endswith(".tar.gz") or raw.endswith(".tgz"):
        return PackSpec(kind="local", path=raw)

    # Split @version pin (version itself never contains '@').
    base, _, version = raw.partition("@")
    if not base or base.startswith("@"):
        raise ValueError(f"invalid SPEC {spec!r}")

    if "/" in base:
        owner, _, repo = base.partition("/")
        if not owner or not repo or "/" in repo:
            raise ValueError(f"invalid github SPEC {spec!r}: want <owner>/<repo>[@version]")
        return PackSpec(kind="github", owner=owner, repo=repo, version=version)
    return PackSpec(kind="shorthand", name=base, version=version)

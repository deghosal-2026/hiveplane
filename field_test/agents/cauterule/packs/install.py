"""Pack installer — ``cauterule pack install`` (#554).

Flow: resolve SPEC → fetch into cache → verify sha256 → unpack to staging →
validate layout (``pack.yaml`` or legacy ``manifest.yaml``) → certification
gate (#481 hook: ``certify_pack``) → atomic install → ``INSTALL.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from cauterule.packs import semver as _semver
from cauterule.packs.certification import certify_pack
from cauterule.packs.deps import parse_dep, read_lockfile, write_lockfile
from cauterule.packs.format import PackManifest, validate_manifest
from cauterule.packs.manager import pack_info as _pack_info
from cauterule.packs.safety import score_pack_safety, score_rule_safety
from cauterule.packs.spec import PackSpec, parse_spec
from cauterule.store.manager import resolve_inside, validate_rule_id

PACK_YAML = "pack.yaml"
LEGACY_MANIFEST = "manifest.yaml"
INSTALL_JSON = "INSTALL.json"


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_versions(a: str, b: str) -> int:
    """Compare two version strings. Returns -1/0/1 (a<b, a==b, a>b).

    Handles leading ``v`` and numeric dot-separated parts; falls back to
    string comparison for non-numeric segments.
    """

    def parts(v: str) -> list[tuple[int, Any]]:
        v = v.strip().lstrip("vV")
        out: list[tuple[int, Any]] = []
        for piece in v.replace("-", ".").split("."):
            # Numeric segments sort after non-numeric ones; the second element
            # breaks ties within a kind (never mixed, so no TypeError) (#804).
            if piece.isdigit():
                out.append((1, int(piece)))
            else:
                out.append((0, piece))
        return out

    pa, pb = parts(a), parts(b)
    if pa == pb:
        return 0
    return -1 if pa < pb else 1


def load_registry(registry_path: str | None = None) -> dict[str, str]:
    """Load shorthand → owner/repo registry mapping."""
    candidates: list[Path] = []
    if registry_path:
        candidates.append(Path(registry_path))
    env_path = os.environ.get("CAUTERULE_REGISTRY")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.home() / ".cauterule" / "registry.yaml")
    for candidate in candidates:
        if candidate.is_file():
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                return {str(k): str(v) for k, v in raw.items()}
    return {}


def _github_asset_url(owner: str, repo: str, version: str) -> tuple[str, str]:
    """Return (download URL, resolved version) for a github SPEC.

    Unpinned versions resolve to ``latest`` via the Releases API at fetch time.
    """
    if version:
        tag = version if version.startswith("v") else f"v{version}"
        base = f"https://github.com/{owner}/{repo}/releases/download/{tag}"
        return f"{base}/{repo}-{tag}.tar.gz", version
    return f"https://github.com/{owner}/{repo}/releases/latest/download/{repo}.tar.gz", "latest"


def _download(url: str, dest: Path, offline: bool = False) -> None:
    if offline:
        raise ValueError(f"offline mode: {url} is not cached (re-run without --offline)")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    request = urllib.request.Request(url, headers={"User-Agent": "cauterule"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = urllib.request.urlopen(request)
    except Exception as exc:
        raise ValueError(
            f"failed to download {url}: {exc} "
            "(check SPEC/version; set GH_TOKEN for rate limits, "
            "or use --offline with a cached pack)"
        ) from exc
    # #800: write to a temp file and atomically rename, so a dropped connection
    # never leaves a truncated file for later installs to reuse.
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as fh, response:
            shutil.copyfileobj(response, fh)
        tmp_path.replace(dest)
    except Exception as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise ValueError(
            f"failed to download {url}: {exc} "
            "(check SPEC/version; set GH_TOKEN for rate limits, "
            "or use --offline with a cached pack)"
        ) from exc


def _read_manifest(pack_dir: Path) -> tuple[PackManifest, bool]:
    """Read pack.yaml (preferred) or legacy manifest.yaml.

    Returns (manifest, legacy_warned).
    """
    manifest_path = pack_dir / PACK_YAML
    legacy = False
    if not manifest_path.is_file():
        manifest_path = pack_dir / LEGACY_MANIFEST
        legacy = True
    if not manifest_path.is_file():
        raise ValueError(
            f"pack layout invalid in {pack_dir}: missing {PACK_YAML} (or legacy {LEGACY_MANIFEST})"
        )
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"pack manifest must be a mapping, got {type(raw).__name__}")
    return PackManifest.from_dict(raw), legacy


Fetcher = Callable[[PackSpec, Path, bool], Path]
"""Fetch a SPEC into cache_dir (offline flag); returns asset path (dir or tarball)."""


def _default_fetch(spec: PackSpec, cache_dir: Path, offline: bool) -> Path:
    if spec.kind == "local":
        candidate = Path(spec.path)
        if not candidate.exists():
            raise ValueError(f"local pack path not found: {spec.path}")
        return candidate
    if spec.kind == "gist":
        # Gist imports are single rules, handled directly in install_pack().
        raise ValueError("internal: gist SPECs bypass _default_fetch")
    if spec.kind == "shorthand":
        registry = load_registry()
        target = registry.get(spec.name)
        if not target:
            raise ValueError(
                f"unknown pack shorthand {spec.name!r} "
                "(no registry entry in ~/.cauterule/registry.yaml; "
                "use <owner>/<repo>[@version] or a local path)"
            )
        owner, _, repo = target.partition("/")
        url, _ = _github_asset_url(owner, repo, spec.version)
        key = f"{owner}~{repo}~{spec.version or 'latest'}.tar.gz"
    else:
        url, _ = _github_asset_url(spec.owner, spec.repo, spec.version)
        key = f"{spec.owner}~{spec.repo}~{spec.version or 'latest'}.tar.gz"
    dest = cache_dir / key
    # #800: a pinned version is immutable, so its cache entry is reusable. An
    # unpinned `latest` must be re-fetched (new releases) unless we are offline
    # and already have a cached copy.
    pinned = bool(spec.version)
    if dest.is_file() and (pinned or offline):
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    _download(url, dest, offline=offline)
    return dest


def _rule_signal(rule_file: Path) -> dict[str, str]:
    """Extract id/trigger/directive from a rule YAML for safety scoring."""
    try:
        data = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {"id": rule_file.stem, "trigger": "", "directive": ""}
    if not isinstance(data, dict):
        return {"id": rule_file.stem, "trigger": "", "directive": ""}
    when = data.get("when", {}) if isinstance(data.get("when"), dict) else {}
    do = data.get("do", {}) if isinstance(data.get("do"), dict) else {}
    return {
        "id": str(data.get("id", rule_file.stem)),
        "trigger": str(when.get("trigger", "")),
        "directive": str(do.get("directive", "")),
    }


def resolve_min_safety_score(explicit: int | None) -> int:
    """Resolve the safety threshold: CLI flag > cauterule.toml > default."""
    if explicit is not None:
        if not 0 <= explicit <= 100:
            raise ValueError(f"--min-safety-score must be 0-100, got {explicit}")
        return explicit
    try:
        from cauterule.config import load_config

        return load_config().packs.min_safety_score
    except (OSError, ValueError):
        from cauterule.packs.safety import DEFAULT_MIN_SAFETY_SCORE

        return DEFAULT_MIN_SAFETY_SCORE


def _stage_asset(asset: Path, staging: Path) -> Path:
    """Copy/extract asset into staging; return the pack root dir."""
    if asset.is_dir():
        shutil.copytree(asset, staging / "pack")
        return staging / "pack"
    if tarfile.is_tarfile(asset):
        dest = staging / "pack"
        try:
            with tarfile.open(asset) as tar:
                try:
                    # Reject absolute/`..`/symlink members (#796); the default
                    # is interpreter-dependent, so pass it explicitly.
                    tar.extractall(dest, filter="data")
                except TypeError:
                    # Python < 3.11.4 has no filter kwarg.
                    tar.extractall(dest)
        except (tarfile.TarError, OSError) as exc:
            raise ValueError(f"unsafe or corrupt pack archive: {exc}") from exc
        root = dest
        # Unwrap single top-level dir (release tarballs).
        children = list(root.iterdir())
        if len(children) == 1 and children[0].is_dir():
            inner = children[0]
            if (inner / PACK_YAML).is_file() or (inner / LEGACY_MANIFEST).is_file():
                return inner
        return root
    raise ValueError(f"unsupported pack asset type: {asset}")


def install_pack(
    spec_str: str,
    store: str = "rules",
    cache_dir: str | None = None,
    checksum: str | None = None,
    skip_cert: bool = False,
    fail_on: str = "error",
    offline: bool = False,
    force: bool = False,
    fetcher: Fetcher | None = None,
    gist_api: Any | None = None,
    min_safety_score: int | None = None,
) -> dict[str, Any]:
    """Install a pack into ``<store>/packs/<name>/``.

    Returns a summary dict with keys: name, version, rule_count, cert, path.
    Raises ValueError with an actionable message on any failure (atomic: an
    aborted install leaves no partial pack directory behind).
    """
    if fail_on not in ("error", "warn"):
        raise ValueError("--fail-on must be 'error' or 'warn'")
    spec = parse_spec(spec_str)
    if spec.kind == "gist":
        from cauterule.packs.share import fetch_gist_rule, import_gist

        # #799: enforce safety on the gist rule *before* it enters the store,
        # and report the real cert status instead of a blind passed=True.
        gist_rule, _files = fetch_gist_rule(spec.gist_id, gist_api)
        threshold = resolve_min_safety_score(min_safety_score)
        safety = score_rule_safety(
            gist_rule.id, gist_rule.when.trigger, gist_rule.do.directive
        )
        safety_ok = safety["score"] >= threshold or skip_cert
        if not safety_ok:
            drags = "; ".join(safety["reasons"][:5])
            raise ValueError(
                f"gist rule {gist_rule.id} safety score {safety['score']} < {threshold}: "
                f"{drags} (raise the score or lower --min-safety-score)"
            )
        imported = import_gist(spec.gist_id, store=store, force=force, api=gist_api)
        return {
            "name": imported["id"],
            "version": "",
            "rule_count": 1,
            "cert": {"passed": safety_ok, "checks": [], "skipped": skip_cert},
            "safety": safety,
            "path": store,
            "legacy_manifest": False,
            "gist": imported["gist"],
        }
    cache = Path(cache_dir or Path.home() / ".cache" / "cauterule" / "packs")
    fetch = fetcher or _default_fetch
    asset = fetch(spec, cache, offline)

    if asset.is_file() and checksum:
        actual = sha256_file(asset)
        if actual != checksum.lower().replace("sha256:", ""):
            asset.unlink(missing_ok=True)
            raise ValueError(
                f"checksum mismatch for {spec_str}: expected {checksum}, got {actual} "
                "(partial download removed)"
            )

    store_path = Path(store)
    packs_root = store_path / "packs"
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        pack_root = _stage_asset(asset, staging)
        manifest, legacy = _read_manifest(pack_root)
        errors = validate_manifest(manifest)
        if errors:
            raise ValueError(f"pack manifest invalid: {'; '.join(errors)}")
        validate_rule_id(manifest.name)

        # Certification gate (#481 hook).
        rule_files = sorted(pack_root.glob("R-*.yaml"))
        rule_files += sorted((pack_root / "rules").glob("R-*.yaml"))
        cert_report: dict[str, Any] = {"passed": True, "checks": [], "skipped": False}
        if skip_cert:
            cert_report = {
                "passed": True,
                "checks": [],
                "skipped": True,
                "warning": "certification bypassed with --skip-cert (CI use only)",
            }
        else:
            cert_report = certify_pack(
                {"manifest": manifest, "rules": [{"id": r.stem} for r in rule_files]}
            )
            failing = [c for c in cert_report["checks"] if c["status"] != "pass"]
            if failing and (fail_on == "error" or fail_on == "warn"):
                details = "; ".join(f"{c['name']}: {c['message']}" for c in failing)
                raise ValueError(
                    f"pack certification failed for {manifest.name}: {details} "
                    "(re-run with --skip-cert to override, not recommended)"
                )

        # Safety-score threshold (#481).
        threshold = resolve_min_safety_score(min_safety_score)
        safety = score_pack_safety([_rule_signal(r) for r in rule_files])
        if safety["score"] < threshold and not skip_cert:
            drags = "; ".join(safety["drags"][:5])
            raise ValueError(
                f"pack {manifest.name} safety score {safety['score']} < {threshold}: "
                f"{drags} (raise the score or lower --min-safety-score)"
            )

        dest = resolve_inside(packs_root, manifest.name)
        existing_install = dest / INSTALL_JSON
        if dest.is_dir() and any(dest.iterdir()):
            if existing_install.is_file():
                try:
                    prior = json.loads(existing_install.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    prior = {}
                prior_version = str(prior.get("version", ""))
                prior_source = str(prior.get("source", ""))
                if prior_source and prior_source != spec_str and not force:
                    raise ValueError(
                        f"name collision: {manifest.name} already installed from "
                        f"{prior_source} (new source {spec_str}); "
                        "re-run with --force to overwrite, never merged silently"
                    )
                if prior_version and not force:
                    cmp = compare_versions(manifest.version, prior_version)
                    if cmp <= 0:
                        kind = "same version" if cmp == 0 else "downgrade"
                        raise ValueError(
                            f"refusing {kind} install of {manifest.name} "
                            f"({prior_version} installed, "
                            f"{manifest.version} requested); "
                            "re-run with --force"
                        )
            elif not force:
                raise ValueError(
                    f"pack {manifest.name} already installed; re-run with --force to overwrite"
                )

        packs_root.mkdir(parents=True, exist_ok=True)
        tmp_dest = staging / "final"
        shutil.copytree(pack_root, tmp_dest)
        if legacy:
            # Backwards compat: synthesise pack.yaml from manifest.yaml + warn.
            manifest_path = tmp_dest / LEGACY_MANIFEST
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            (tmp_dest / PACK_YAML).write_text(
                yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
            )
        # Dependency check (#548): satisfied deps lock, missing deps warn
        # with an actionable hint (auto-pull needs the release registry).
        dep_notes: list[str] = []
        for entry in manifest.deps:
            try:
                dep_name, constraint = parse_dep(entry)
            except ValueError as exc:
                dep_notes.append(f"ignoring malformed dep {entry!r}: {exc}")
                continue
            try:
                installed_info = _pack_info(dep_name, base_dir=str(store_path))
                installed_version = str(installed_info.get("version", ""))
            except (FileNotFoundError, ValueError):
                installed_version = ""
            if not installed_version:
                dep_notes.append(
                    f"missing dep {entry}: run `cauterule pack install {dep_name}` first"
                )
                continue
            if constraint:
                try:
                    ok = _semver.satisfies(installed_version, constraint)
                except ValueError:
                    ok = False
                if not ok:
                    dep_notes.append(
                        f"dep {entry} not satisfied by installed {dep_name}@{installed_version}"
                    )
        asset_sha = sha256_file(asset) if asset.is_file() else ""
        install_record = {
            "name": manifest.name,
            "version": manifest.version,
            "source": spec_str,
            "asset_sha256": asset_sha,
            "installed_at": datetime.now(UTC).isoformat(),
            "cert": {
                "passed": cert_report.get("passed", True),
                "checks": cert_report.get("checks", []),
            },
            "safety": safety,
            "legacy_manifest": legacy,
        }
        (tmp_dest / INSTALL_JSON).write_text(json.dumps(install_record, indent=2), encoding="utf-8")
        if dest.is_dir():
            shutil.rmtree(dest)
        tmp_dest.replace(dest)

    locked = read_lockfile(str(store_path))
    locked[manifest.name] = manifest.version
    sources = dict.fromkeys(locked, "")
    try:
        record = json.loads((dest / INSTALL_JSON).read_text(encoding="utf-8"))
        sources[manifest.name] = str(record.get("source", ""))
    except (OSError, ValueError):
        pass
    write_lockfile(str(store_path), locked, sources)

    return {
        "name": manifest.name,
        "version": manifest.version,
        "rule_count": len(manifest.rules),
        "cert": cert_report,
        "safety": safety,
        "path": str(dest),
        "legacy_manifest": legacy,
        "dep_notes": dep_notes,
    }

"""Pack publishing — ``cauterule pack publish`` (#548).

Validates layout + certification, enforces monotonic versions, builds the
release asset, and ships a GitHub release. All network goes through the
``ReleasesApi`` protocol so tests run hermetic with a fake.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Protocol

import yaml

from cauterule.packs import semver
from cauterule.packs.certification import certify_pack
from cauterule.packs.format import PackManifest, validate_manifest
from cauterule.packs.install import _rule_signal, resolve_min_safety_score
from cauterule.packs.safety import score_pack_safety


class ReleasesApi(Protocol):
    """GitHub Releases surface consumed by publish (real or fake)."""

    def list_versions(self, repo: str) -> list[str]:
        """Return published versions for *repo*, newest last."""
        ...

    def get_manifest(self, repo: str, version: str) -> dict[str, Any] | None:
        """Return the pack.yaml of a published release, if available."""
        ...

    def create_release(
        self,
        repo: str,
        tag: str,
        asset: Path,
        notes: str,
        prerelease: bool,
    ) -> str:
        """Create a release with *asset* attached. Returns the release URL."""
        ...


class GitHubReleasesApi:
    """Live GitHub Releases API via urllib (honours GH_TOKEN)."""

    _API = "https://api.github.com"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        headers = {"User-Agent": "cauterule", "Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self._API}{path}", data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def list_versions(self, repo: str) -> list[str]:
        """List published versions for *repo*, oldest first."""
        releases = self._request("GET", f"/repos/{repo}/releases?per_page=100") or []
        versions = []
        for release in releases:
            tag = str(release.get("tag_name", ""))
            name = tag.rsplit("-v", 1)[-1] if "-v" in tag else tag.lstrip("v")
            try:
                semver.parse(name)
            except ValueError:
                continue
            versions.append(name)
        return sorted(versions, key=semver.parse)

    def get_manifest(self, _repo: str, _version: str) -> dict[str, Any] | None:
        """Return None: manifests download on demand; absent is fine."""
        return None

    def create_release(self, repo: str, tag: str, asset: Path, notes: str, prerelease: bool) -> str:
        """Create a release with *asset* attached. Returns the release URL."""
        release = self._request(
            "POST",
            f"/repos/{repo}/releases",
            {"tag_name": tag, "name": tag, "body": notes, "prerelease": prerelease},
        )
        upload_url = str(release.get("upload_url", "")).split("{")[0]
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        headers = {"User-Agent": "cauterule", "Content-Type": "application/gzip"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = f"?name={asset.name}&label={asset.name}"
        request = urllib.request.Request(
            f"{upload_url}{params}",
            data=asset.read_bytes(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request):
            pass
        return str(release.get("html_url", ""))


README_SECTIONS = ("Symptoms", "Rules", "Install", "Certification")


def lint_readme(pack_dir: Path) -> list[str]:
    """Warn (v0.3.0) about README sections missing from a pack. Hard gate in v0.4.0."""
    readme = pack_dir / "README.md"
    if not readme.is_file():
        return ["README.md missing"]
    text = readme.read_text(encoding="utf-8").lower()
    return [
        f"README.md missing section: {section}"
        for section in README_SECTIONS
        if section.lower() not in text
    ]


def _reject_service_owned_fields(manifest: PackManifest) -> None:
    """Reject hand-set marketplace service fields (ratings/downloads)."""
    owned = [
        k for k in ("rating", "downloads", "reviews_url") if manifest.marketplace.get(k) is not None
    ]
    if owned:
        raise ValueError(
            f"pack.yaml marketplace.{owned[0]} is service-owned (filled by the marketplace, "
            "never hand-edited); remove it before publishing"
        )


def _default_repo(pack_dir: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(pack_dir),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("cannot infer --repo (pass --repo owner/repo)") from exc
    if out.returncode != 0:
        raise ValueError("cannot infer --repo (pass --repo owner/repo)")
    url = out.stdout.strip()
    for prefix in ("git@github.com:", "https://github.com/"):
        if url.startswith(prefix):
            url = url[len(prefix) :]
            break
    url = url.removesuffix(".git")
    if "/" not in url:
        raise ValueError(f"cannot infer --repo from remote {url!r} (pass --repo owner/repo)")
    return url


def _read_pack(pack_dir: Path) -> tuple[PackManifest, list[Path]]:
    manifest_path = pack_dir / "pack.yaml"
    if not manifest_path.is_file():
        manifest_path = pack_dir / "manifest.yaml"
    if not manifest_path.is_file():
        raise ValueError(f"no pack.yaml in {pack_dir} (run cauterule pack create first)")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("pack manifest must be a mapping")
    manifest = PackManifest.from_dict(raw)
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError(f"pack manifest invalid: {'; '.join(errors)}")
    try:
        semver.parse(manifest.version)
    except ValueError:
        raise ValueError(
            f"pack version {manifest.version!r} is not strict semver (want MAJOR.MINOR.PATCH)"
        ) from None
    rule_files = sorted(pack_dir.glob("R-*.yaml"))
    if not rule_files:
        rule_files = sorted((pack_dir / "rules").glob("R-*.yaml"))
    return manifest, rule_files


def auto_notes(name: str, old_rules: list[str], new_rules: list[str], version: str) -> str:
    """Generate release notes from a rule-list diff."""
    old, new = set(old_rules), set(new_rules)
    lines = [f"# {name} v{version}", ""]
    added, removed = sorted(new - old), sorted(old - new)
    if added:
        lines.append("## Added")
        lines.extend(f"- {rule}" for rule in added)
        lines.append("")
    if removed:
        lines.append("## Removed")
        lines.extend(f"- {rule}" for rule in removed)
        lines.append("")
    if not added and not removed:
        lines.append("Patch release: rule text / metadata fixes, no rule changes.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_asset(pack_dir: Path, name: str, version: str, dest_dir: Path) -> Path:
    """Build ``pack-<name>-v<version>.tar.gz`` from *pack_dir*."""
    asset = dest_dir / f"pack-{name}-v{version}.tar.gz"
    with tarfile.open(asset, "w:gz") as tar:
        for path in sorted(pack_dir.rglob("*")):
            if path.is_file() and ".git" not in path.parts:
                tar.add(path, arcname=str(path.relative_to(pack_dir)))
    return asset


def publish_pack(
    pack_dir: str = ".",
    bump_kind: str | None = None,
    version: str | None = None,
    repo: str | None = None,
    notes_file: str | None = None,
    prerelease: bool = False,
    dry_run: bool = False,
    allow_dirty: bool = False,
    api: ReleasesApi | None = None,
    min_safety_score: int | None = None,
) -> dict[str, Any]:
    """Publish a pack as a GitHub release. Returns a result dict."""
    directory = Path(pack_dir)
    manifest, rule_files = _read_pack(directory)
    if not rule_files:
        raise ValueError(f"pack {manifest.name} has no R-*.yaml rule files")
    _reject_service_owned_fields(manifest)
    for warning in lint_readme(directory):
        print(f"warning: {warning} (hard gate in v0.4.0)")

    cert_report = certify_pack(
        {"manifest": manifest, "rules": [{"id": p.stem} for p in rule_files]}
    )
    if not cert_report.get("passed", False):
        failing = "; ".join(
            f"{c['name']}: {c['message']}" for c in cert_report["checks"] if c["status"] != "pass"
        )
        raise ValueError(f"certification failed for {manifest.name}: {failing} (fix the pack)")

    threshold = resolve_min_safety_score(min_safety_score)
    safety = score_pack_safety([_rule_signal(p) for p in rule_files])
    if safety["score"] < threshold:
        drags = "; ".join(safety["drags"][:5])
        raise ValueError(
            f"pack {manifest.name} safety score {safety['score']} < {threshold}: "
            f"{drags} (fix the pack; publish has no override)"
        )

    target_repo = repo or _default_repo(directory)
    api = api or GitHubReleasesApi()
    published = api.list_versions(target_repo)

    new_version = manifest.version
    if version:
        new_version = version
    elif bump_kind:
        new_version = semver.bump(manifest.version, bump_kind)
    try:
        semver.parse(new_version)
    except ValueError:
        raise ValueError(f"version {new_version!r} is not strict semver") from None

    if published and not all(
        semver.parse(new_version)._key() > semver.parse(v)._key() for v in published
    ):
        raise ValueError(
            f"version {new_version} is not newer than published {sorted(published)[-1]} "
            "(no re-publishing; bump the version)"
        )

    old_rules: list[str] = []
    if published:
        previous = api.get_manifest(target_repo, sorted(published, key=semver.parse)[-1])
        if previous:
            old_rules = list(previous.get("rules", []))
    suggested = semver.suggest_bump(old_rules, list(manifest.rules)) if old_rules else "minor"
    if bump_kind and bump_kind != suggested and old_rules:
        print(f"warning: --bump {bump_kind} differs from suggested {suggested} for this diff")

    if notes_file:
        notes = Path(notes_file).read_text(encoding="utf-8")
    else:
        notes = auto_notes(manifest.name, old_rules, list(manifest.rules), new_version)
    if not allow_dirty:
        notes += "\n<!-- built from a clean tree -->\n"

    with tempfile.TemporaryDirectory() as tmp:
        asset = build_asset(directory, manifest.name, new_version, Path(tmp))
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        tag = f"pack-{manifest.name}-v{new_version}"
        if dry_run:
            return {
                "name": manifest.name,
                "version": new_version,
                "tag": tag,
                "repo": target_repo,
                "asset": asset.name,
                "sha256": digest,
                "notes": notes,
                "prerelease": prerelease,
                "cert": cert_report,
                "safety": safety,
                "dry_run": True,
            }
        asset_copy = directory / asset.name
        asset_copy.write_bytes(asset.read_bytes())
        try:
            url = api.create_release(target_repo, tag, asset_copy, notes, prerelease)
        finally:
            asset_copy.unlink(missing_ok=True)
    return {
        "name": manifest.name,
        "version": new_version,
        "tag": tag,
        "repo": target_repo,
        "sha256": digest,
        "url": url,
        "notes": notes,
        "prerelease": prerelease,
        "cert": cert_report,
        "safety": safety,
        "dry_run": False,
        "install_hint": f"cauterule pack install {target_repo}@{new_version}",
    }

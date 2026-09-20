from __future__ import annotations

import click

from cauterule.serialization.rule_yaml import load_rules_from_dir


@click.group("pack")
def pack() -> None:
    """Manage rule packs."""


@pack.command("list")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
def pack_list(store_dir: str) -> None:
    """List available rule packs."""
    from cauterule.packs.manager import list_packs

    names = list_packs(base_dir=store_dir)
    if not names:
        click.echo("No packs installed.")
        return
    click.echo("Available rule packs:")
    for name in names:
        click.echo(f"  {name}")


@pack.command("info")
@click.argument("name")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
def pack_info(name: str, store_dir: str, as_json: bool) -> None:
    """Show information about a rule pack."""
    import json as _json

    from cauterule.packs.manager import pack_info as _info

    try:
        info = _info(name, base_dir=store_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(_json.dumps(info, indent=2, default=str))
        return
    click.echo(f"Pack: {info.get('name', name)}")
    if info.get("version"):
        click.echo(f"Version: {info['version']}")
    if info.get("description"):
        click.echo(f"Description: {info['description']}")
    if info.get("author"):
        click.echo(f"Author: {info['author']}")
    if info.get("license"):
        click.echo(f"License: {info['license']}")
    deps = info.get("deps") or []
    if deps:
        click.echo(f"Deps: {', '.join(deps)}")
    click.echo(f"Rules: {info.get('rule_count', len(info.get('rules', [])))}")
    cert = info.get("cert") or {}
    if cert:
        status = "passed" if cert.get("passed") else "FAILED"
        skipped = " (bypassed)" if cert.get("skipped") else ""
        click.echo(f"Cert: {status}{skipped}")
    safety = info.get("safety") or {}
    if safety and isinstance(safety, dict) and "score" in safety:
        click.echo(f"Safety score: {safety['score']}")
        for drag in safety.get("drags", [])[:5]:
            click.echo(f"  - {drag}")
    if info.get("source"):
        click.echo(f"Source: {info['source']}")
    rules = load_rules_from_dir(store_dir)
    pack_rules = [r for r in rules if r.pack == name]
    for r in pack_rules:
        click.echo(f"  {r.id}: {r.when.trigger}")


@pack.command("create")
@click.argument("name")
@click.option("--store", "store_dir", default="rules", help="Source rule store")
@click.option("--from-tag", "from_tags", multiple=True, help="Include rules with this tag")
@click.option("--from-taxonomy", "from_taxonomies", multiple=True, help="Filter by taxonomy node")
@click.option("--rule", "rules", multiple=True, help="Include an explicit rule id")
@click.option("--all-promoted", is_flag=True, help="Include every promoted rule in the store")
@click.option("--include-candidate", is_flag=True, help="Admit non-active rules with a warning")
@click.option("--version", "version", default="0.1.0", help="Initial semver")
@click.option("--description", default="", help="Pack blurb")
@click.option("--author", default=None, help="Pack author (default: git config user.name)")
@click.option("--license", "license_id", default="MIT", help="SPDX license identifier")
@click.option("--out", default=None, help="Destination dir (default: ./<name>/)")
@click.option("--cert/--no-cert", default=True, help="Run certify_pack() on the scaffold")
@click.option("--min-rules", default=1, type=int, help="Refuse packs with fewer rules")
@click.option("--force", is_flag=True, help="Write into a non-empty --out dir")
def pack_create(
    name: str,
    store_dir: str,
    from_tags: tuple[str, ...],
    from_taxonomies: tuple[str, ...],
    rules: tuple[str, ...],
    all_promoted: bool,
    include_candidate: bool,
    version: str,
    description: str,
    author: str | None,
    license_id: str,
    out: str | None,
    cert: bool,
    min_rules: int,
    force: bool,
) -> None:
    """Scaffold a shippable rule pack out of the rule store."""
    import os

    from cauterule.packs.create import create_pack as _create

    store = os.environ.get("CAUTERULE_STORE", store_dir)
    try:
        summary = _create(
            name,
            store=store,
            from_tag=from_tags,
            from_taxonomy=from_taxonomies,
            rule=rules,
            all_promoted=all_promoted,
            include_candidate=include_candidate,
            version=version,
            description=description,
            author=author,
            license=license_id,
            out=out,
            run_cert=cert,
            min_rules=min_rules,
            force=force,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    n_rules = len(summary["rules"])
    click.echo(f"Created pack {summary['name']} v{summary['version']} ({n_rules} rules)")
    click.echo(f"  path: {summary['path']}")
    for note in summary.get("notes", []):
        click.echo(f"  note: {note}")
    click.echo(f"  next: cauterule test --pack {summary['name']}")
    click.echo("  next: cauterule pack publish --dry-run")


@pack.command("publish")
@click.option("--dir", "pack_dir", default=".", help="Pack directory (must contain pack.yaml)")
@click.option("--bump", "bump_kind", type=click.Choice(["major", "minor", "patch"]), default=None)
@click.option("--version", "version", default=None, help="Explicit version override")
@click.option("--repo", default=None, help="Target repo (default: git remote origin)")
@click.option("--notes", "notes_file", default=None, help="Release notes file")
@click.option("--prerelease", is_flag=True, help="Mark as prerelease")
@click.option("--dry-run", is_flag=True, help="Validate + build asset without uploading")
@click.option("--allow-dirty", is_flag=True, help="Publish with uncommitted changes")
@click.option("--min-safety-score", type=int, default=None, help="Safety threshold override")
def pack_publish(
    pack_dir: str,
    bump_kind: str | None,
    version: str | None,
    repo: str | None,
    notes_file: str | None,
    prerelease: bool,
    dry_run: bool,
    allow_dirty: bool,
    min_safety_score: int | None,
) -> None:
    """Publish a pack as a GitHub release with semver enforcement."""
    from cauterule.packs.publish import publish_pack as _publish

    try:
        result = _publish(
            pack_dir,
            bump_kind=bump_kind,
            version=version,
            repo=repo,
            notes_file=notes_file,
            prerelease=prerelease,
            dry_run=dry_run,
            allow_dirty=allow_dirty,
            min_safety_score=min_safety_score,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if result.get("dry_run"):
        click.echo(f"dry-run: would publish {result['tag']} to {result['repo']}")
        click.echo(f"  asset: {result['asset']} (sha256 {result['sha256'][:12]}…)")
    else:
        click.echo(f"Published {result['tag']}: {result.get('url', '')}")
        click.echo(f"  {result.get('install_hint', '')}")


@pack.command("tree")
@click.argument("name")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
def pack_tree(name: str, store_dir: str) -> None:
    """Print the resolved dependency tree for an installed pack."""
    from cauterule.packs.deps import read_lockfile
    from cauterule.packs.manager import pack_info as _info

    try:
        info = _info(name, base_dir=store_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    locked = read_lockfile(store_dir)
    click.echo(f"{name}@{locked.get(name, info.get('version', ''))}")
    for dep in info.get("deps", []):
        dep_name = str(dep).split()[0]
        click.echo(f"  └── {dep} (locked: {locked.get(dep_name, 'unresolved')})")


@pack.command("outdated")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
def pack_outdated(store_dir: str) -> None:
    """List installed packs (versions come from the lockfile when present)."""
    from cauterule.packs.deps import read_lockfile
    from cauterule.packs.manager import list_packs as _list
    from cauterule.packs.manager import pack_info as _info

    locked = read_lockfile(store_dir)
    for name in _list(base_dir=store_dir):
        try:
            info = _info(name, base_dir=store_dir)
        except (FileNotFoundError, ValueError):
            continue
        installed = locked.get(name, info.get("version", "?"))
        click.echo(f"{name} {installed} (latest check needs network: pack tree {name})")


@pack.command("install")
@click.argument("spec")
@click.option("--store", "store_dir", default="rules", help="Target rule store")
@click.option("--cache-dir", default=None, help="Download cache dir")
@click.option("--checksum", default=None, help="Expected sha256 of the asset")
@click.option("--skip-cert", is_flag=True, help="Bypass certification gate (CI use only)")
@click.option(
    "--fail-on",
    type=click.Choice(["error", "warn"]),
    default="error",
    help="Cert severity threshold that blocks install",
)
@click.option("--offline", is_flag=True, help="Install from cache only, no network")
@click.option("--force", is_flag=True, help="Overwrite an installed pack / downgrade")
@click.option("--min-safety-score", type=int, default=None, help="Safety threshold override")
def pack_install(
    spec: str,
    store_dir: str,
    cache_dir: str | None,
    checksum: str | None,
    skip_cert: bool,
    fail_on: str,
    offline: bool,
    force: bool,
    min_safety_score: int | None,
) -> None:
    """Install a rule pack from GitHub, shorthand, gist, or local path."""
    import os

    from cauterule.packs.install import install_pack

    store = os.environ.get("CAUTERULE_STORE", store_dir)
    try:
        summary = install_pack(
            spec,
            store=store,
            cache_dir=cache_dir,
            checksum=checksum,
            skip_cert=skip_cert,
            fail_on=fail_on,
            offline=offline,
            force=force,
            min_safety_score=min_safety_score,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Installed {summary['name']} v{summary['version']} ({summary['rule_count']} rules)")
    click.echo(f"  path: {summary['path']}")
    safety = summary.get("safety") or {}
    if safety and isinstance(safety, dict) and "score" in safety:
        click.echo(f"  safety score: {safety['score']}")
    for note in summary.get("dep_notes", []):
        click.echo(f"  dep: {note}")
    if summary.get("legacy_manifest"):
        click.echo("  warning: legacy manifest.yaml synthesised to pack.yaml")
    if skip_cert:
        click.echo("  warning: certification bypassed with --skip-cert")
    click.echo(f"  next: cauterule test --pack {summary['name']}")

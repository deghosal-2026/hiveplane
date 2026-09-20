from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import click

from cauterule.serialization.trajectory_jsonl import load_trajectories

CORPUS_ROOT = Path("corpus")
LINT_REQUIRED = ("failure_class", "quality_label", "domain")


def _corpus_files(domain: str | None = None) -> list[Path]:
    root = CORPUS_ROOT / "public"
    if not root.is_dir():
        return []
    pattern = f"{domain}/*.jsonl" if domain else "**/*.jsonl"
    return sorted(p for p in root.glob(pattern) if p.is_file())


def _read_lines(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (parsed dicts, errors) for a jsonl file."""
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{lineno}: invalid JSON ({exc})")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path}:{lineno}: line must decode to a mapping")
            continue
        entries.append(data)
    return entries, errors


@click.group("corpus")
def corpus() -> None:
    """Manage the trajectory corpus (add/list/validate/lint/build/export)."""


@corpus.command("add")
@click.argument("source")
@click.option("--domain", default="raw", help="Target domain under corpus/public/")
@click.option("--tags", default="", help="Comma-separated tags to stamp on ingest")
def corpus_add(source: str, domain: str, tags: str) -> None:
    """Ingest a jsonl file or directory into the corpus."""
    src = Path(source)
    if not src.exists():
        msg = f"source not found: {source}"
        raise click.ClickException(msg)
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*.jsonl") if p.is_file())
    if not files:
        msg = f"no .jsonl files under {source}"
        raise click.ClickException(msg)
    dest_dir = CORPUS_ROOT / "public" / domain
    dest_dir.mkdir(parents=True, exist_ok=True)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    ingested = 0
    for path in files:
        entries, errors = _read_lines(path)
        if errors:
            msg = f"refusing {path}: {errors[0]}"
            raise click.ClickException(msg)
        if tag_list:
            for entry in entries:
                entry_tags = entry.get("tags", [])
                entry["tags"] = sorted(set(entry_tags) | set(tag_list))
        dest = dest_dir / path.name
        with dest.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")
        ingested += len(entries)
    click.echo(f"Ingested {ingested} trajectories from {len(files)} file(s) into {dest_dir}")


@corpus.command("list")
@click.option("--domain", default=None, help="Filter by domain")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def corpus_list(domain: str | None, fmt: str) -> None:
    """List corpus files with trajectory counts."""
    files = _corpus_files(domain)
    rows = []
    for path in files:
        entries, _ = _read_lines(path)
        rows.append({"file": str(path), "trajectories": len(entries)})
    if fmt == "json":
        click.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("No corpus files found.")
        return
    click.echo(f"{'file':60s} trajectories")
    for row in rows:
        click.echo(f"{row['file']:60s} {row['trajectories']}")


@corpus.command("validate")
@click.argument("files", nargs=-1)
def corpus_validate(files: tuple[str, ...]) -> None:
    """Schema-check corpus files (Trajectory parsing)."""
    targets = [Path(f) for f in files] or _corpus_files()
    if not targets:
        click.echo("No corpus files found.")
        return
    failures = 0
    for path in targets:
        _, json_errors = _read_lines(path)
        if json_errors:
            click.echo(f"FAIL {path}: {json_errors[0]}")
            failures += 1
            continue
        try:
            trajs = list(load_trajectories(path))
        except Exception as exc:
            click.echo(f"FAIL {path}: {exc}")
            failures += 1
            continue
        click.echo(f"OK {path}: {len(trajs)} trajectories")
    if failures:
        msg = f"{failures} file(s) failed validation"
        raise click.ClickException(msg)


@corpus.command("lint")
@click.argument("files", nargs=-1)
def corpus_lint(files: tuple[str, ...]) -> None:
    """Style + provenance check (required annotation fields)."""
    targets = [Path(f) for f in files] or _corpus_files()
    if not targets:
        click.echo("No corpus files found.")
        return
    issues = 0
    for path in targets:
        entries, errors = _read_lines(path)
        for error in errors:
            click.echo(f"ERROR {error}")
            issues += 1
        for lineno, entry in enumerate(entries, 1):
            missing = [k for k in LINT_REQUIRED if not entry.get(k)]
            if missing:
                click.echo(f"LINT {path}:{lineno}: missing {', '.join(missing)}")
                issues += 1
    if issues:
        click.echo(f"{issues} lint issue(s)")
    else:
        click.echo("Corpus lint clean.")


@corpus.command("build")
@click.option("--output", default="corpus/store.jsonl", help="Consolidated output path")
def corpus_build(output: str) -> None:
    """Consolidate corpus files into one indexed store."""
    files = _corpus_files()
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with out.open("w", encoding="utf-8") as fh:
        for path in files:
            entries, _ = _read_lines(path)
            for entry in entries:
                entry.setdefault("_source", str(path))
                fh.write(json.dumps(entry) + "\n")
                total += 1
    click.echo(f"Built {out} with {total} trajectories from {len(files)} file(s)")


@corpus.command("export")
@click.option("--format", "fmt", type=click.Choice(["jsonl", "csv"]), default="jsonl")
@click.option("--output", default="-", help="Output path ('-' for stdout)")
@click.option("--domain", default=None, help="Filter by domain")
def corpus_export(fmt: str, output: str, domain: str | None) -> None:
    """Export corpus entries as jsonl or csv."""
    entries: list[dict[str, Any]] = []
    for path in _corpus_files(domain):
        file_entries, _ = _read_lines(path)
        entries.extend(file_entries)
    if fmt == "jsonl":
        text = "".join(json.dumps(e) + "\n" for e in entries)
    else:
        buf = io.StringIO()
        fieldnames = sorted({k for e in entries for k in e})
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            flat = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in entry.items()}
            writer.writerow(flat)
        text = buf.getvalue()
    if output == "-":
        click.echo(text, nl=False)
    else:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Exported {len(entries)} entries to {output}")

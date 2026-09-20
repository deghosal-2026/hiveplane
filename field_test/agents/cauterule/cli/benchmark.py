from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from cauterule.benchmark.leaderboard import Leaderboard

BENCHMARKS_DIR = Path("benchmarks")
COMPARE_SCRIPT = Path("scripts/compare_benchmarks.py")


def _discover() -> list[str]:
    """Return benchmark names (test_<name>.py stems) under benchmarks/."""
    if not BENCHMARKS_DIR.is_dir():
        return []
    return sorted(p.stem[len("test_") :] for p in BENCHMARKS_DIR.glob("test_*.py"))


@click.group("benchmark")
def benchmark() -> None:
    """Run performance benchmarks (extraction/replay/injection hot paths)."""


@benchmark.command("list")
@click.option("--format", "fmt", type=click.Choice(["table", "leaderboard"]), default="table")
def benchmark_list(fmt: str) -> None:
    """List available benchmarks."""
    names = _discover()
    if not names:
        click.echo("No benchmarks found under benchmarks/.")
        return
    if fmt == "leaderboard":
        board = Leaderboard(title="Benchmarks")
        board.add_entries([{"name": name, "score": "not run"} for name in names])
        board.sort(key="name", reverse=False)
        click.echo(board.render(), nl=False)
        return
    click.echo("Available benchmarks:")
    for name in names:
        click.echo(f"  {name}")


@benchmark.command("run")
@click.argument("name", required=False)
@click.option("--all", "run_all", is_flag=True, help="Run the full suite")
@click.option("--compare", "baseline", default=None, help="Baseline json for delta comparison")
def benchmark_run(name: str | None, run_all: bool, baseline: str | None) -> None:
    """Run a benchmark by name, or --all."""
    names = _discover()
    if run_all:
        targets = [str(BENCHMARKS_DIR)]
    elif name:
        if name not in names:
            msg = f"unknown benchmark {name!r} (see: cauterule benchmark list)"
            raise click.ClickException(msg)
        targets = [str(BENCHMARKS_DIR / f"test_{name}.py")]
    else:
        msg = "pass a benchmark NAME or use --all"
        raise click.ClickException(msg)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "--benchmark-only",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, nl=False)
    if result.returncode != 0:
        msg = f"benchmark run failed (exit {result.returncode})"
        raise click.ClickException(msg)
    if baseline:
        if not COMPARE_SCRIPT.is_file():
            msg = "scripts/compare_benchmarks.py not found (ships with #605)"
            raise click.ClickException(msg)
        cmp_cmd = [sys.executable, str(COMPARE_SCRIPT), baseline, ".benchmarks"]
        cmp_result = subprocess.run(cmp_cmd, capture_output=True, text=True, timeout=120)
        click.echo(cmp_result.stdout, nl=False)
        if cmp_result.returncode != 0:
            msg = "benchmark regression detected (see deltas above)"
            raise click.ClickException(msg)

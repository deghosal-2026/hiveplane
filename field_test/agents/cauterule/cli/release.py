"""Release helpers — version consistency checks and publish guidance (#604)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import click

from cauterule import __version__

_VERSION_FILE = "pyproject.toml"


def _find_pyproject(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / _VERSION_FILE
        if candidate.is_file():
            return candidate
    return None


def _pyproject_version(path: Path) -> str:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return str(data.get("project", {}).get("version", ""))


@click.group("release")
def release() -> None:
    """Release helpers — version checks and publish guidance."""


@release.command("check")
def release_check() -> None:
    """Verify version consistency and print the release steps."""
    pyproject = _find_pyproject()
    if pyproject is None:
        raise click.ClickException("pyproject.toml not found")  # noqa: TRY003
    declared = _pyproject_version(pyproject)
    click.echo(f"pyproject.toml: {declared or '<missing>'}")
    click.echo(f"cauterule.__version__: {__version__}")
    if not declared:
        raise click.ClickException("pyproject.toml has no [project].version")  # noqa: TRY003
    if declared != __version__:
        raise click.ClickException(  # noqa: TRY003
            f"version mismatch: pyproject.toml={declared!r} package={__version__!r}"
        )
    click.echo("[ok] versions consistent")
    click.echo("")
    click.echo("Next steps:")
    click.echo(f"  git tag -a v{declared} -m 'Cauterule v{declared}'")
    click.echo(f"  git push origin v{declared}")
    click.echo("  # the Release workflow builds, twine-checks, and publishes")

"""``plancritic quickstart`` — scaffold and run the first live-provider plan.

This command gives end users a one-command path from provider coordinates to a
real planning run: it writes a minimal project scaffold, binds planner/critic
to the chosen provider, and executes the packaged quickstart goal.
"""

from __future__ import annotations

import argparse
import tempfile
from importlib.resources import files
from pathlib import Path

from ..llm.registry import ProviderRegistry
from .init import run_init
from .plan import run_plan

DEFAULT_PROVIDER_NAME = "quickstart"


def _default_goal() -> str:
    """The packaged quickstart goal bundled inside ``planner_critic/demo``."""
    resource = files("planner_critic.demo").joinpath("data", "quickstart.json")
    return str(Path(str(resource)))


def build_quickstart_parser() -> argparse.ArgumentParser:
    """Build the ``quickstart`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic quickstart",
        description="Scaffold and run the packaged quickstart against a live provider",
        add_help=False,
    )
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible endpoint base URL")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--api-key", default=None, help="Optional API key")
    parser.add_argument("--goal", default=_default_goal(), help="Goal JSON path")
    parser.add_argument(
        "--dir",
        default="",
        help="Workspace directory (default: a temporary directory)",
    )
    return parser


def run_quickstart(argv: list[str]) -> int:
    """Scaffold a project, bind the chosen provider, and run the quickstart goal."""
    args = build_quickstart_parser().parse_args(argv)
    workspace = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="plancritic-qs-"))

    rc = run_init(["--dir", str(workspace), "--force"])
    if rc != 0:
        return rc

    config_path = workspace / "plancritic.toml"
    registry = ProviderRegistry.load(config_path)
    for role in ("planner", "critic"):
        registry.add(
            DEFAULT_PROVIDER_NAME,
            base_url=args.base_url,
            model=args.model,
            api_key=args.api_key,
            role=role,
        )
    registry.save()

    return run_plan(
        [
            str(args.goal),
            "--config",
            str(config_path),
            "--store",
            str(workspace / ".plancritic" / "plans.db"),
        ]
    )


__all__ = ["build_quickstart_parser", "run_quickstart"]

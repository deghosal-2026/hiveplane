from __future__ import annotations

import json
from pathlib import Path

import click

from cauterule.config import load_config
from cauterule.extraction.dryrun import dry_run as dry_run_fn
from cauterule.extraction.gate import GateMode, run_gate
from cauterule.extraction.multipass import multipass_extract
from cauterule.extraction.tournament import run_tournament
from cauterule.llm import get_llm
from cauterule.models.trajectory import Trajectory
from cauterule.serialization.trajectory_jsonl import load_trajectories


@click.command("extract")
@click.argument("trajectory")
@click.option("--dry-run", is_flag=True, help="Show candidates without persisting.")
def extract(trajectory: str, dry_run: bool) -> None:
    """Extract candidate rules from a failure trajectory."""
    path = Path(trajectory)
    try:
        if path.suffix == ".jsonl":
            trajs = list(load_trajectories(path))
            if not trajs:
                click.echo("No trajectories found in file.")
                return
            traj = trajs[0]
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            traj = (
                Trajectory.from_dict(raw)
                if isinstance(raw, dict)
                else Trajectory.from_dict(
                    {"id": "cli", "timestamp": "", "task": "", "steps": [], "success": False}
                )
            )
    except OSError as e:
        msg = f"Cannot read trajectory {path}: {e}"
        raise click.ClickException(msg) from e

    cfg = load_config()
    gate_mode: GateMode = "relaxed" if cfg.extraction.gate_mode == "relaxed" else "strict"
    if dry_run:
        gate_result = run_gate(traj, mode=gate_mode)
        if not gate_result.should_extract:
            click.echo("pre-extraction drop: no failure signal (silence)")
            click.echo(f"silence reason: {gate_result.reason}")
            return
        info = dry_run_fn(traj, template=None)
        click.echo(f"dry-run candidate when: {info.when.trigger}")
        click.echo(f"dry-run candidate do: {info.do.directive}")
        click.echo(f"dry-run confidence: {info.confidence}")
        if info.reasoning:
            click.echo(f"dry-run reasoning:\n{info.reasoning}")
        return

    llm = get_llm(cfg)
    gate_result = run_gate(traj, mode=gate_mode)
    if not gate_result.should_extract:
        click.echo("No candidates extracted (pre-extraction drop: no failure signal).")
        return
    candidates = multipass_extract(
        traj, llm, temperatures=cfg.extraction.temperatures, gate_mode=cfg.extraction.gate_mode
    )
    if not candidates:
        click.echo("No candidates extracted.")
        return

    click.echo(f"Extracted {len(candidates)} candidate(s):")
    for i, c in enumerate(candidates, start=1):
        click.echo(f"  #{i}: when={c.when.trigger} | do={c.do.directive} | conf={c.confidence:.2f}")

    trajectories = (
        list(load_trajectories(Path(cfg.paths.trajectories)))
        if Path(cfg.paths.trajectories).is_file()
        else []
    )
    ranked = run_tournament(candidates, trajectories)
    if ranked:
        click.echo("Tournament results (best first):")
        for r in ranked:
            click.echo(
                f"  Rank #{r.rank}: {r.candidate.when.trigger} "
                f"(precision={r.evidence.precision:.2f}, "
                f"recall={r.evidence.recall:.2f}, verdict={r.evidence.verdict})"
            )

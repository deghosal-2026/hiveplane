from __future__ import annotations

import time

import click

from cauterule.capture.failure import detect_failure_class
from cauterule.capture.metadata import enrich_trajectory
from cauterule.models.trajectory import Step, Trajectory
from cauterule.serialization.trajectory_jsonl import dump_trajectories


def _seed_failure(task: str, tool: str, error: str, step_number: int = 1) -> Trajectory:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    step = Step(step_number=step_number, tool=tool, input=task, output="", error=error)
    raw = Trajectory(
        id=f"demo-{task[:10].lower().replace(' ', '-')}",
        timestamp=now,
        task=task,
        steps=(step,),
        success=False,
        failure_point=f"step_{step_number}",
        failure_class=detect_failure_class((step,)) or "unknown",
    )
    return enrich_trajectory(raw)


def _run_demo_pipeline(failures: int) -> None:
    seeded = [
        _seed_failure("deploy to production fails due to missing env", "deploy", "ENV_VAR not set"),
        _seed_failure("docker build fails on network timeout", "docker", "network timed out"),
        _seed_failure("git push rejected non-fast-forward", "git", "rejected non-fast-forward"),
        _seed_failure("python import error for missing module", "python", "ModuleNotFoundError"),
        _seed_failure("test suite fails on assertion", "pytest", "AssertionError"),
    ]
    for i in range(failures - len(seeded)):
        seeded.append(
            _seed_failure(
                f"failure scenario {i + 6}",
                "tool",
                f"error code {i + 1}",
            )
        )

    click.echo(f"Seeded {len(seeded)} failure trajectories.")

    from pathlib import Path

    traj_dir = Path("trajectories")
    traj_dir.mkdir(parents=True, exist_ok=True)
    traj_path = traj_dir / "demo.jsonl"
    dump_trajectories(seeded, traj_path)
    click.echo(f"Wrote trajectories to {traj_path}")

    click.echo(f"\n{'=' * 60}")
    click.echo("Extraction phase (dry-run):")
    from cauterule.extraction.dryrun import dry_run

    for t in seeded[: min(3, len(seeded))]:
        info = dry_run(t)
        click.echo(f"  Trajectory '{t.id}':")
        click.echo(f"    when: {info.when.trigger}")
        click.echo(f"    do: {info.do.directive}")

    click.echo(f"\n{'=' * 60}")
    click.echo("Promotion phase (no LLM — skipping):")
    click.echo("Run `cauterule extract <trajectory.jsonl>` with a configured LLM to promote rules.")


@click.command("demo")
@click.option("--failures", default=5, help="Number of seeded failures.")
def demo(failures: int) -> None:
    """Seeded failure history with a full narrated loop walkthrough."""
    _run_demo_pipeline(failures)

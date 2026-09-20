from __future__ import annotations

from pathlib import Path

import click

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.report import build_evidence_report
from cauterule.serialization.trajectory_jsonl import load_trajectories


@click.command("test")
@click.argument("rule", required=False)
@click.option("--pack", "pack_name", default=None, help="Replay-test every rule in a pack.")
@click.option("--store", "store_dir", default="rules", help="Rule store directory")
@click.option("--ci", is_flag=True, help="Output JUnit XML.")
def test(rule: str | None, pack_name: str | None, store_dir: str, ci: bool) -> None:
    """Replay-test a candidate rule against historical failures."""
    from cauterule.store.manager import StoreManager

    if pack_name:
        _test_pack(pack_name, store_dir, ci)
        return
    if not rule:
        msg = "pass a RULE id or use --pack <name>"
        raise click.ClickException(msg)
    store = StoreManager(base_dir=store_dir)
    candidate = store.get_rule(rule)
    if candidate:
        cand = CandidateRule(
            when=candidate.when,
            do=candidate.do,
            confidence=candidate.confidence,
            reasoning=candidate.provenance.source_trajectory,
        )
    else:
        click.echo(f"Rule {rule!r} not found in store.")
        return

    traj_path = Path("trajectories")
    trajectories: list[Trajectory] = []
    if traj_path.is_dir():
        for f in traj_path.glob("*.jsonl"):
            trajectories.extend(load_trajectories(f))
    elif traj_path.with_suffix(".jsonl").is_file():
        trajectories = list(load_trajectories(traj_path.with_suffix(".jsonl")))

    if not trajectories:
        click.echo("No trajectories found for replay testing.")
        return

    report_ = build_evidence_report(cand, trajectories)
    # #542: recording point — replay verdicts attributed to this standing rule
    # are accumulated into its outcome counters/trend via the idempotent log.
    try:
        from cauterule.observe.outcomes import apply_report_outcomes

        apply_report_outcomes(store, rule, report_)
    except Exception:
        pass
    click.echo(f"Testing rule: {rule}")
    click.echo(f"  Failures prevented: {len(report_.failures_prevented)}")
    click.echo(f"  Successes broken: {len(report_.successes_broken)}")
    click.echo(f"  Precision: {report_.precision:.2f}")
    click.echo(f"  Recall: {report_.recall:.2f}")
    click.echo(f"  Verdict: {report_.verdict}")
    if report_.verdict == "inconclusive" and report_.inconclusive_reason:
        click.echo(f"  Inconclusive reason: {report_.inconclusive_reason}")
    if report_.failures_prevented:
        click.echo(f"  Prevented: {', '.join(report_.failures_prevented)}")
    if report_.successes_broken:
        click.echo(f"  Broken: {', '.join(report_.successes_broken)}")


def _test_pack(pack_name: str, store_dir: str, ci: bool) -> None:
    """Replay-test every rule in *pack_name* against its bundled fixtures."""
    from cauterule.packs.loader import load_pack
    from cauterule.replay.matcher import match_score
    from cauterule.serialization.trajectory_jsonl import load_trajectories

    try:
        manifest, rules = load_pack(pack_name, base_dir=store_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    pack_dir = Path(store_dir) / "packs" / pack_name
    fixture_path = pack_dir / "tests" / f"replay_{pack_name}.jsonl"
    if not fixture_path.is_file():
        msg = f"pack {pack_name} ships no {fixture_path}"
        raise click.ClickException(msg)
    trajectories = list(load_trajectories(fixture_path))
    import json as _json

    rule_ids = [
        _json.loads(line).get("rule_id", "")
        for line in fixture_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    failures = 0
    for rule in rules:
        own = [t for t, rid in zip(trajectories, rule_ids, strict=False) if rid in ("", rule.id)]
        if not own:
            own = list(trajectories)
        cand = CandidateRule(when=rule.when, do=rule.do, confidence=rule.confidence, reasoning=None)
        hits = sum(1 for t in own if match_score(cand, t) >= 0.5)
        status = "PASS" if hits >= 1 else "FAIL"
        if hits < 1:
            failures += 1
        click.echo(f"  [{status}] {rule.id}: {hits}/{len(own)} fixtures matched")
    click.echo(f"Pack {manifest.name}: {len(rules) - failures}/{len(rules)} rules green")
    if ci:
        click.echo(f"<testsuite name={pack_name!r} tests={len(rules)} failures={failures}>")
    if failures:
        msg = f"{failures} rule(s) failed replay in {pack_name}"
        raise click.ClickException(msg)

from __future__ import annotations

import json

import click

from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen
from cauterule.replay.rewind import rewind
from cauterule.serialization.trajectory_jsonl import load_trajectories
from cauterule.store.manager import StoreManager


@click.command("rewind")
@click.argument("trajectory")
@click.option("--rule-id", default=None, help="Rule id from the store to overlay.")
@click.option("--trigger", default=None, help="Ad-hoc trigger (with --directive).")
@click.option("--directive", default=None, help="Ad-hoc directive (with --trigger).")
@click.option("--rules-dir", default="rules", help="Rule store directory.")
def rewind_cmd(
    trajectory: str, rule_id: str | None, trigger: str | None, directive: str | None, rules_dir: str
) -> None:
    """Failure Time Machine — replay TRAJECTORY (JSONL) with a rule overlay."""
    try:
        traj = next(iter(load_trajectories(trajectory)))
    except StopIteration:
        msg = f"no trajectories in {trajectory}"
        raise click.ClickException(msg) from None
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    candidate: CandidateRule | None = None
    if rule_id is not None:
        store = StoreManager(base_dir=rules_dir)
        try:
            rule = store.get_rule(rule_id)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if rule is None:
            msg = f"rule not found: {rule_id}"
            raise click.ClickException(msg)
        candidate = CandidateRule(
            when=RuleWhen(trigger=rule.when.trigger, context=rule.when.context),
            do=RuleDo(directive=rule.do.directive, because=rule.do.because),
            confidence=rule.confidence,
        )
    elif trigger is not None and directive is not None:
        candidate = CandidateRule(
            when=RuleWhen(trigger=trigger), do=RuleDo(directive=directive), confidence=1.0
        )
    else:
        msg = "provide --rule-id or both --trigger and --directive"
        raise click.ClickException(msg)

    result = rewind(traj, candidate)
    click.echo(json.dumps(result, indent=2, default=str))

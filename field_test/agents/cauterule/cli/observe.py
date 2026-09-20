from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import click

from cauterule.cli.frontier import frontier
from cauterule.cli.gaps import gaps
from cauterule.cli.journal import journal
from cauterule.cli.metrics import metrics
from cauterule.store.manager import StoreManager


def _parse_since(value: str | None) -> datetime | None:
    """Parse --since values like 7d, 24h, 30m into a cutoff datetime."""
    if not value:
        return None
    text = value.strip().lower()
    try:
        if text.endswith("d"):
            delta = timedelta(days=int(text[:-1]))
        elif text.endswith("h"):
            delta = timedelta(hours=int(text[:-1]))
        elif text.endswith("m"):
            delta = timedelta(minutes=int(text[:-1]))
        else:
            delta = timedelta(days=int(text))
    except ValueError:
        msg = f"invalid --since {value!r}: want like 7d, 24h, 30m"
        raise click.ClickException(msg) from None
    return datetime.now(UTC) - delta


def _promoted_at(rule: object) -> datetime | None:
    raw = getattr(rule, "promoted_at", None)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def build_summary(store_dir: str, since: str | None = None) -> dict[str, Any]:
    """Build the observe summary payload for *store_dir*."""
    from cauterule.observe.coverage_frontier import suggest_next_frontier
    from cauterule.observe.coverage_gap import find_coverage_gaps
    from cauterule.store.health import health_report

    cutoff = _parse_since(since)
    store = StoreManager(base_dir=store_dir)
    rules = store.list_rules()
    if cutoff:
        epoch = datetime.min.replace(tzinfo=UTC)
        rules = [r for r in rules if (_promoted_at(r) or epoch) >= cutoff]
    prevented = sum(r.prevented_count for r in rules)
    broke = sum(r.broke_count for r in rules)
    neutral = sum(r.neutral_count for r in rules)
    report = health_report(base_dir=store_dir)
    gaps_list = find_coverage_gaps(store, [])
    try:
        frontier_text = suggest_next_frontier(store, [])
    except Exception:
        frontier_text = "unavailable"
    return {
        "rules_learned": len(rules),
        "by_status": report.get("by_status", {}),
        "verdicts": {"prevented": prevented, "broke": broke, "neutral": neutral},
        "top_gaps": gaps_list[:5],
        "frontier": frontier_text,
        "since": since,
    }


@click.group("observe", invoke_without_command=True)
@click.option("--store-dir", default="rules", help="Rule store directory")
@click.option("--since", default=None, help="Filter by period (e.g. 7d, 24h)")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable summary")
@click.pass_context
def observe(ctx: click.Context, store_dir: str, since: str | None, as_json: bool) -> None:
    """Observability summary; subcommands: journal, metrics, frontier, gaps."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        summary = build_summary(store_dir, since)
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(summary, indent=2, default=str))
        return
    period = f" (since {since})" if since else ""
    click.echo(f"Rules learned{period}: {summary['rules_learned']}")
    verdicts = summary["verdicts"]
    click.echo(
        f"Recent verdicts: prevented={verdicts['prevented']} "
        f"broke={verdicts['broke']} neutral={verdicts['neutral']}"
    )
    gaps_list = summary["top_gaps"]
    if gaps_list:
        click.echo("Top gaps:")
        for gap in gaps_list:
            click.echo(f"  {gap.get('domain')}: {gap.get('failure_count')} failures, no rule")
    else:
        click.echo("Top gaps: none reported")
    click.echo(f"Frontier: {summary['frontier']}")


observe.add_command(metrics)
observe.add_command(journal)
observe.add_command(frontier)
observe.add_command(gaps)

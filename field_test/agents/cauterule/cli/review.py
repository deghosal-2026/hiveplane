from __future__ import annotations

import json

import click

from cauterule.store.manager import StoreManager


@click.command("review")
@click.option("--filter", "filter_tag", default=None, help="Filter by tag, e.g. --filter tag=git")
@click.option(
    "--status", default=None, help="Filter by status: pass/inconclusive/fail or active/retired"
)
@click.option("--confidence", default=None, help="Confidence range e.g. 0.7-1.0")
@click.option("--batch", is_flag=True, help="Non-interactive batch review mode")
@click.option("--export-annotations", is_flag=True, help="Export annotations as JSON")
@click.option("--json", "as_json", is_flag=True, help="Output review queue as JSON")
@click.option("--store-dir", default="rules", help="Rule store directory")
def review(
    filter_tag: str | None,
    status: str | None,
    confidence: str | None,
    batch: bool,
    export_annotations: bool,
    as_json: bool,
    store_dir: str,
) -> None:
    """Launch the TUI review interface (Textual).

    Browse, approve, reject candidates. Supports filtering and batch mode.
    """
    store = StoreManager(base_dir=store_dir)
    rules = store.list_rules()

    # Parse --filter tag=VALUE
    tag_val: str | None = None
    if filter_tag:
        if "=" in filter_tag:
            _, tag_val = filter_tag.split("=", 1)
        else:
            tag_val = filter_tag
        tag_val = tag_val.strip().lower()

    # Parse confidence range
    min_conf: float | None = None
    max_conf: float | None = None
    if confidence and "-" in confidence:
        try:
            lo, hi = confidence.split("-", 1)
            min_conf = float(lo)
            max_conf = float(hi)
        except ValueError:
            raise click.BadParameter(  # noqa: TRY003
                "confidence must be like 0.7-1.0"
            ) from None
    elif confidence:
        try:
            min_conf = float(confidence)
        except ValueError:
            raise click.BadParameter("confidence must be numeric") from None  # noqa: TRY003

    # Apply filters
    filtered = []
    for r in rules:
        if tag_val and not any(tag_val in t.lower() for t in r.tags):
            continue
        if status and status != "all" and r.status != status:
            continue
        if min_conf is not None and r.confidence < min_conf:
            continue
        if max_conf is not None and r.confidence > max_conf:
            continue
        filtered.append(r)

    if as_json or batch or export_annotations:
        out = [
            {
                "id": r.id,
                "trigger": r.when.trigger,
                "directive": r.do.directive,
                "confidence": r.confidence,
                "status": r.status,
                "tags": list(r.tags),
                "hit_count": r.hit_count,
                "last_match": r.last_match,
            }
            for r in filtered
        ]
        if batch:
            click.echo(json.dumps({"queue_size": len(out), "candidates": out[:10]}, indent=2))
            if not as_json:
                click.echo(f"Batch review: {len(out[:10])} candidates queued")
            return
        if export_annotations:
            # Export annotations stored alongside rules (tags/comments)
            click.echo(json.dumps(out, indent=2))
            return
        click.echo(json.dumps(out, indent=2))
        return

    # Interactive TUI
    from cauterule.tui.app import CauterRuleApp

    app = CauterRuleApp()
    app.run()

"""CLI entry point for loopguard log analysis.

Provides the ``loopguard analyze`` command for inspecting escalation
logs.  Parses JSONL files produced by EscalationLogger and prints
summarised metrics: total escalations, cost breakdown, and most common
trigger types (FR-6.2).

Supports basic filtering by trigger type, date range, and escalation
model name (FR-6.3 — basic filters for v0.1.0).

Usage::

    loopguard analyze logs/escalations.jsonl
    loopguard analyze logs/escalations.jsonl --trigger repeated_error
    loopguard analyze logs/escalations.jsonl --since 2026-07-01
    loopguard analyze logs/escalations.jsonl --model gpt-4

Uses Click for CLI argument parsing (Typer 0.25.1 has a positional
argument bug on Python 3.14; Click is Typer's underlying engine and
works correctly).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

# Click is used instead of Typer because Typer 0.25.1 has a positional
# argument bug on Python 3.14 (raises SystemExit on `--help`).  Click is
# Typer's underlying engine and works correctly.  No migration cost since
# loopguard has only one CLI command.
import click

from ai_loopguard.logging import EscalationEvent


class EventLog:
    """Container for parsed escalation events with filtering logic.

    Holds a list of EscalationEvent objects loaded from a JSONL log file
    and provides methods to filter and summarise them.

    Design note: only EscalationEvent lines are parsed into _escalations.
    CappedEvent and FailOpenEvent lines are silently skipped during
    parsing because the CLI summary focuses on escalation metrics.
    A future version could surface capped/fail-open counts separately.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        """Initialise with raw parsed JSONL lines.

        Args:
            events: List of event dicts parsed from JSONL lines.

        """
        self._escalations: list[EscalationEvent] = []

        # Try to parse each raw dict as an EscalationEvent.
        # Lines that don't match (CappedEvent, FailOpenEvent, or
        # malformed data) are silently skipped.
        for raw in events:
            event = self._parse_event(raw)
            if event is not None:
                self._escalations.append(event)

    @staticmethod
    def _parse_event(raw: dict[str, Any]) -> EscalationEvent | None:
        """Try to parse a raw dict into an EscalationEvent.

        Skips lines that don't match the EscalationEvent schema
        (e.g. CappedEvent or FailOpenEvent lines, or malformed data).

        Args:
            raw: A parsed JSON object from a JSONL line.

        Returns:
            An EscalationEvent if the dict has the required fields,
            or None if parsing fails.

        """
        try:
            return EscalationEvent(**raw)
        except (ValueError, TypeError):
            # Pydantic ValidationError is a subclass of ValueError,
            # so this catches both schema mismatches and type errors.
            return None

    # filter_trigger, filter_since, filter_model all mutate _escalations
    # in-place.  When called sequentially (as in the analyze command),
    # they accumulate: each filter narrows the set (AND logic).  Users
    # who want OR logic should call analyze separately per filter.

    def filter_trigger(self, trigger_type: str) -> None:
        """Filter events to only those with the given trigger type.

        Args:
            trigger_type: The trigger type to filter by (e.g.
                ``"repeated_error"``, ``"test_failure"``).

        """
        # Replace the internal list in place so that chained filter
        # calls accumulate (AND logic).
        self._escalations = [
            e for e in self._escalations if e.trigger_type == trigger_type
        ]

    def filter_since(self, since: datetime) -> None:
        """Filter events to only those on or after the given datetime.

        Args:
            since: Timestamp threshold (inclusive).

        """
        # Convert datetime to Unix timestamp for comparison with
        # the event's timestamp field (which is a float).
        ts = since.timestamp()
        self._escalations = [e for e in self._escalations if e.timestamp >= ts]

    def filter_model(self, model_name: str) -> None:
        """Filter events to only those with the given escalation model.

        Performs a case-insensitive substring match so that
        ``--model gpt-4`` matches ``"gpt-4-turbo"`` etc.

        Args:
            model_name: The model name or substring to filter by.

        """
        # Substring match (not exact) so users can search by vendor
        # (e.g., "gpt" matches all GPT variants) or by exact name.
        pattern = model_name.lower()
        self._escalations = [
            e
            for e in self._escalations
            if pattern in e.escalation_model.lower()
        ]

    @property
    def total_escalations(self) -> int:
        """Total number of escalation events after filtering."""
        return len(self._escalations)

    @property
    def total_cost(self) -> float:
        """Total cost across all filtered escalation events."""
        return sum(e.total_task_cost_usd for e in self._escalations)

    @property
    def trigger_breakdown(self) -> Counter[str]:
        """Count of events by trigger type."""
        return Counter(e.trigger_type for e in self._escalations)

    @property
    def cost_by_trigger(self) -> dict[str, float]:
        """Total cost grouped by trigger type."""
        result: dict[str, float] = {}
        for e in self._escalations:
            result[e.trigger_type] = (
                result.get(e.trigger_type, 0.0) + e.total_task_cost_usd
            )
        return result

    @property
    def events(self) -> list[EscalationEvent]:
        """Return a copy of the filtered escalation events.

        Returns a defensive copy so callers cannot mutate the internal
        list.
        """
        return list(self._escalations)


def load_jsonl(path: str) -> list[dict[str, Any]]:
    """Load and parse a JSONL file into a list of event dicts.

    Malformed lines are skipped with a warning.

    Args:
        path: Path to the JSONL file.

    Returns:
        List of parsed event dicts (one per valid JSONL line).

    """
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        # enumerate from 1 so line numbers in warnings are human-readable
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            # Skip blank lines silently — they're common in log files
            # that are actively being written to (partial writes).
            # Also skips lines that are being appended concurrently.
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # Warn but continue — one bad line shouldn't prevent
                # analysis of the rest of the log.  Partial writes from
                # concurrent loggers are the most common cause.
                click.echo(
                    f"Warning: skipping malformed line {line_no}", err=True
                )
    return events


def print_summary(log: EventLog) -> None:
    """Print a formatted summary of escalation events.

    Displays total escalations, total cost, cost breakdown by trigger
    type, and most common trigger types.

    Args:
        log: The filtered EventLog to summarise.

    """
    click.echo("=" * 50)
    click.echo("loopguard escalation summary")
    click.echo("=" * 50)

    # Early exit for empty logs — avoid division-by-zero and
    # meaningless "0" output.
    if log.total_escalations == 0:
        click.echo("No escalations found.")
        return

    click.echo(f"Total escalations:       {log.total_escalations}")
    click.echo(f"Total cost (USD):        ${log.total_cost:.4f}")

    # Cost breakdown by trigger type — shows which triggers are most
    # expensive, helping users prioritise which patterns to fix first.
    click.echo("")
    click.echo("Cost breakdown by trigger:")
    cost_by_trigger = log.cost_by_trigger
    # most_common() sorts by count descending; we look up the cost
    # for each trigger from the pre-computed dict.
    for trigger, count in log.trigger_breakdown.most_common():
        cost = cost_by_trigger.get(trigger, 0.0)
        click.echo(f"  {trigger:25s}  {count:3d}  ${cost:.4f}")


@click.command()
# exists=True so Click validates the file exists before the command
# runs — gives a clean error message instead of a traceback.
@click.argument("log_file", type=click.Path(exists=True))
# --summary/--no-summary flag pair: default True so users get output
# without extra flags.  --no-summary is useful for scripting.
@click.option(
    "--summary/--no-summary",
    default=True,
    help="Print summary table (default: on).",
)
@click.option(
    "--trigger",
    default=None,
    help="Filter by trigger type (e.g. repeated_error).",
)
@click.option(
    "--since",
    default=None,
    help="Filter to events on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--model",
    default=None,
    help="Filter by escalation model name (substring match).",
)
def analyze(
    log_file: str,
    summary: bool,
    trigger: str | None,
    since: str | None,
    model: str | None,
) -> None:
    """Analyze loopguard escalation logs from a JSONL file.

    Parses the JSONL log file, applies optional filters, and prints a
    summary of escalation metrics.

    The command flow is:
        1. Load raw JSONL → list of dicts (skip malformed lines)
        2. Parse into EventLog (skips CappedEvent/FailOpenEvent lines)
        3. Apply filters sequentially (AND logic)
        4. Print summary (unless --no-summary)
    """
    # Step 1: Load raw JSONL lines into dicts.
    raw_events = load_jsonl(log_file)

    # Step 2: Parse dicts into EscalationEvent objects (skipping
    # CappedEvent/FailOpenEvent lines).
    log = EventLog(raw_events)

    # Step 3: Apply filters in sequence (AND logic — each filter
    # narrows the result set further).  Order doesn't matter for
    # correctness (filter intersection is commutative).
    if trigger is not None:
        log.filter_trigger(trigger)
    if since is not None:
        # Parse the date string and convert to UTC datetime.
        # If parsing fails, print an error and exit with code 1
        # (distinct from Click's code 2 for usage errors).
        try:
            dt = datetime.strptime(since, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            click.echo(
                f"Error: invalid date format '{since}'. "
                f"Use YYYY-MM-DD.",
                err=True,
            )
            raise click.exceptions.Exit(1) from None
        log.filter_since(dt)
    if model is not None:
        log.filter_model(model)

    # Step 4: Print summary (unless --no-summary was passed).
    if summary:
        print_summary(log)


# Module-level callable for setuptools entry point.
# pyproject.toml has: loopguard = "ai_loopguard.cli:cli"
# This makes `loopguard analyze ...` work after pip install.
cli = analyze

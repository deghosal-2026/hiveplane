"""Run-to-run diff over state, tool calls, model calls, cost, and outcome (M60-02)."""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import cast

from pydantic import JsonValue

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import Run
from hiveplane.core.usage import UsageReport
from hiveplane.replay.models import FieldDelta, RunDiff


def diff_runs(
    before_run: Run,
    before_events: Sequence[RunEvent],
    before_usage: Sequence[UsageReport],
    after_run: Run,
    after_events: Sequence[RunEvent],
    after_usage: Sequence[UsageReport],
) -> RunDiff:
    """Compare two runs and surface the meaningful differences between them."""
    before_tools = _tool_calls(before_events)
    after_tools = _tool_calls(after_events)
    before_models = _model_calls(before_usage)
    after_models = _model_calls(after_usage)
    deltas = [
        delta
        for delta in (
            _field("state", before_run.state, after_run.state),
            _field("result", before_run.result, after_run.result),
            _field("failure_reason", before_run.failure_reason, after_run.failure_reason),
            _field("model_identity", before_run.model_identity, after_run.model_identity),
        )
        if delta is not None
    ]
    tools_added = [call for call in after_tools if call not in before_tools]
    tools_removed = [call for call in before_tools if call not in after_tools]
    models_added = [call for call in after_models if call not in before_models]
    models_removed = [call for call in before_models if call not in after_models]
    cost_delta = after_run.cost_usd - before_run.cost_usd
    latency_delta = _latency_ms(after_run) - _latency_ms(before_run)
    identical = not any(
        (
            deltas,
            tools_added,
            tools_removed,
            models_added,
            models_removed,
            cost_delta,
            latency_delta,
        )
    )
    return RunDiff(
        source_run_id=before_run.id,
        target_run_id=after_run.id,
        identical=identical,
        state_before=before_run.state,
        state_after=after_run.state,
        result_changed=before_run.result != after_run.result,
        failure_changed=before_run.failure_reason != after_run.failure_reason,
        cost_delta_usd=round(cost_delta, 10),
        latency_delta_ms=latency_delta,
        tool_calls_added=tools_added,
        tool_calls_removed=tools_removed,
        model_calls_added=models_added,
        model_calls_removed=models_removed,
        field_deltas=deltas,
    )


def _field(field: str, before: object, after: object) -> FieldDelta | None:
    if before == after:
        return None
    return FieldDelta(field=field, before=_json_value(before), after=_json_value(after))


def _json_value(value: object) -> JsonValue | None:
    if isinstance(value, Enum):
        return str(value.value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, dict)):
        return cast(JsonValue, value)
    return str(value)


def _tool_calls(events: Sequence[RunEvent]) -> list[dict[str, JsonValue]]:
    return [
        {"event": event.type.value, "detail": event.detail}
        for event in sorted(events, key=lambda item: item.sequence)
        if event.type is EventType.TOOL_CALL
    ]


def _model_calls(usage: Sequence[UsageReport]) -> list[dict[str, JsonValue]]:
    return [
        {
            "model_identity": report.model_identity,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "cost_usd": report.cost_usd,
        }
        for report in usage
    ]


def _latency_ms(run: Run) -> int:
    if run.started_at is None or run.finished_at is None:
        return 0
    return max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))

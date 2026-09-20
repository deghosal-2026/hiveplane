"""Cost measurement for the field test (#653/#486; plan §5.4/§7.4).

Computes the per-model cost metrics reported in
``docs/field-test/v0.3.0/cost-measurement.md``:

    cost_per_candidate     = total_cost / candidates_produced
    cost_per_promoted_rule = total_cost / rules_promoted
    cost_per_1k_trajectories = total_cost / trajectories * 1000
    gate_savings           = gate_dropped * cost_per_request

Cost is token-based when input/output prices are supplied, otherwise it falls
back to ``cost_per_request_usd x llm_requests`` — the flat estimate used by
``cauterule preflight``. The fallback exists because the OMLX local tier is
$0 while the cloud tier is priced per request, and token usage is only
recorded when the provider returns it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """Pricing model for a single sweep.

    ``input_price_per_1k``/``output_price_per_1k`` are USD per 1,000 tokens. When
    both are zero, ``cost_per_request_usd`` (flat per LLM call) is used instead.
    """

    input_price_per_1k: float = 0.0
    output_price_per_1k: float = 0.0
    cost_per_request_usd: float = 0.0

    @property
    def token_based(self) -> bool:
        """True when token prices are set (token cost supersedes per-request)."""
        return self.input_price_per_1k > 0.0 or self.output_price_per_1k > 0.0


@dataclass(frozen=True)
class CostReport:
    """Aggregate cost metrics for a corpus sweep."""

    trajectories: int
    llm_requests: int
    candidates_produced: int
    rules_promoted: int
    gate_dropped: int
    prompt_tokens: int
    completion_tokens: int
    total_cost_usd: float
    cost_per_candidate: float
    cost_per_promoted_rule: float
    cost_per_1k_trajectories: float
    gate_savings_usd: float


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _get(record: dict[str, object], key: str, default: int = 0) -> int:
    value = record.get(key, default)
    return _as_int(value, default)


def measure_cost(
    records: list[dict[str, object]],
    *,
    cost_model: CostModel | None = None,
) -> CostReport:
    """Aggregate cost metrics across per-trajectory *records*.

    Each record provides ``status``, ``llm_requests``, ``candidates_produced``,
    ``promoted``, ``prompt_tokens``, ``completion_tokens`` (all optional except
    ``status``). Records with ``status == "gate_dropped"`` never call the LLM.
    """
    model = cost_model or CostModel()
    trajectories = len(records)
    llm_requests = sum(_get(r, "llm_requests") for r in records)
    candidates = sum(_get(r, "candidates_produced") for r in records)
    promoted = sum(_get(r, "promoted") for r in records)
    gate_dropped = sum(1 for r in records if r.get("status") == "gate_dropped")
    prompt_tokens = sum(_get(r, "prompt_tokens") for r in records)
    completion_tokens = sum(_get(r, "completion_tokens") for r in records)

    if model.token_based:
        total_cost = (
            prompt_tokens / 1000.0 * model.input_price_per_1k
            + completion_tokens / 1000.0 * model.output_price_per_1k
        )
    else:
        total_cost = llm_requests * model.cost_per_request_usd

    return CostReport(
        trajectories=trajectories,
        llm_requests=llm_requests,
        candidates_produced=candidates,
        rules_promoted=promoted,
        gate_dropped=gate_dropped,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost_usd=total_cost,
        cost_per_candidate=(total_cost / candidates) if candidates else 0.0,
        cost_per_promoted_rule=(total_cost / promoted) if promoted else 0.0,
        cost_per_1k_trajectories=(total_cost / trajectories * 1000) if trajectories else 0.0,
        gate_savings_usd=gate_dropped * model.cost_per_request_usd,
    )


def records_from_results(
    results: list[dict[str, object]],
    *,
    extraction_passes: int = 2,
) -> list[dict[str, object]]:
    """Map raw ``results.jsonl`` records to cost-measurement records.

    ``llm_requests`` is the number of extraction calls attempted: zero for
    gate-dropped trajectories, otherwise ``extraction_passes``. ``promoted``
    counts candidates whose replay verdict is ``pass`` (the best candidate).
    """
    records: list[dict[str, object]] = []
    for result in results:
        status = str(result.get("status", ""))
        if status == "gate_dropped":
            requests = 0
            candidates = 0
        else:
            requests = _as_int(result.get("llm_requests"), extraction_passes)
            candidates = _as_int(result.get("candidate_count"))
        promoted = _as_int(result.get("promoted"))
        if not promoted:
            best = result.get("best")
            if isinstance(best, dict) and best.get("verdict") == "pass":
                promoted = 1
        usage_raw = result.get("usage")
        usage: dict[object, object] = usage_raw if isinstance(usage_raw, dict) else {}
        records.append(
            {
                "status": status,
                "llm_requests": requests,
                "candidates_produced": candidates,
                "promoted": promoted,
                "prompt_tokens": _as_int(result.get("prompt_tokens") or usage.get("prompt_tokens")),
                "completion_tokens": _as_int(
                    result.get("completion_tokens") or usage.get("completion_tokens")
                ),
            }
        )
    return records

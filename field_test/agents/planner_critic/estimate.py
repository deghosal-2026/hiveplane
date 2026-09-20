"""Deterministic complexity / cost estimate (F-17, PRD §2.7d).

Before a user approves, PlannerCritic surfaces a derived summary computed with
**zero LLM cost**: step count, parallel-branch count, irreversible-op count,
estimated LLM calls, and estimated token cost. The user can gate on cost, not
just risk — and the estimate feeds the budget check.

The model is a deterministic proxy. `est_llm_calls` models the loop cost:
one planner decomposition + one audit per revision (capped), plus optional
re-audits; `est_token_cost` multiplies per-call token estimates by the
configured price. Exactness is not required — the estimate must stay within
±20% of actual on the corpus fixtures (§7).
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema.goal import Budget
from .schema.plan import PlanVersion, RiskClass
from .types import PlanComplexity

# Token-cost model (per-call). These are calibrated constants, not config —
# a real cost model (M3+) can replace them without changing the interface.
TOKENS_PER_PLANNER_CALL = 400
TOKENS_PER_CRITIC_CALL = 600
PRICE_PER_1K_TOKENS = 0.001  # USD per 1k tokens (placeholder for local models)


@dataclass(frozen=True)
class EstimateConfig:
    """Tuning for the deterministic cost estimate.

    Attributes:
        revision_cap: Assumed worst-case revisions (mirrors the loop cap).
        price_per_1k_tokens: USD per 1000 tokens for the token-cost line.
    """

    revision_cap: int = 3
    price_per_1k_tokens: float = PRICE_PER_1K_TOKENS


def estimate_complexity(plan: PlanVersion, config: EstimateConfig | None = None) -> PlanComplexity:
    """Compute the deterministic complexity summary for a plan (F-17).

    Args:
        plan: The plan revision to summarize.
        config: Estimate tuning (revision cap, price); defaults applied.

    Returns:
        A :class:`PlanComplexity` with step/branch/irreversible counts and the
        estimated LLM calls + token cost.
    """
    cfg = config or EstimateConfig()

    step_count = len(plan.tasks)
    parallel_branch_count = len({t.parallel_group for t in plan.tasks if t.parallel_group})
    irreversible_op_count = sum(
        1 for t in plan.tasks if t.risk_class in (RiskClass.HIGH, RiskClass.CRITICAL)
    )

    est_llm_calls = _estimate_llm_calls(plan, cfg.revision_cap)
    est_token_cost = _estimate_token_cost(est_llm_calls, cfg.price_per_1k_tokens)

    return PlanComplexity(
        step_count=step_count,
        parallel_branch_count=parallel_branch_count,
        irreversible_op_count=irreversible_op_count,
        est_llm_calls=est_llm_calls,
        est_token_cost=est_token_cost,
    )


def _estimate_llm_calls(plan: PlanVersion, revision_cap: int) -> int:
    """Worst-case LLM calls for the loop over this plan.

    Args:
        plan: The plan being estimated.
        revision_cap: The loop's revision cap.

    Returns:
        Planner decomposition (1) + one critic audit per revision (capped) +
        one revision per revise step. On the root revision the whole plan is
        audited once; later revisions re-audit only changed tasks (best
        effort, still charged a full call for the cap estimate).
    """
    # 1 planner decompose + 1 critic audit on revision 1
    calls = 2
    # each subsequent revision: 1 planner revise + 1 critic audit
    calls += 2 * (revision_cap - 1)
    return calls


def _estimate_token_cost(est_llm_calls: int, price_per_1k_tokens: float) -> float:
    """Convert the call estimate to a dollar figure.

    Args:
        est_llm_calls: Estimated number of LLM calls.
        price_per_1k_tokens: USD per 1000 tokens.

    Returns:
        The estimated cost in USD.
    """
    avg_tokens = (TOKENS_PER_PLANNER_CALL + TOKENS_PER_CRITIC_CALL) / 2
    total_tokens = est_llm_calls * avg_tokens
    return round((total_tokens / 1000) * price_per_1k_tokens, 4)


def within_budget(budget: Budget | None, complexity: PlanComplexity) -> bool:
    """Whether the plan's estimated spend fits the goal budget.

    Args:
        budget: The goal's spend ceiling (optional fields = uncapped).
        complexity: The deterministic complexity summary.

    Returns:
        True when every set ceiling is satisfied by the estimate.
    """
    if budget is None:
        return True
    if budget.max_calls is not None and complexity.est_llm_calls > budget.max_calls:
        return False
    if budget.max_tokens is not None:
        est_tokens = complexity.est_llm_calls * (
            (TOKENS_PER_PLANNER_CALL + TOKENS_PER_CRITIC_CALL) / 2
        )
        if est_tokens > budget.max_tokens:
            return False
    return True

"""Deterministic tools for the request-triage demo agent.

Tools are pure functions of their inputs: the same account/intent always returns
the same result, so run outcomes are fully reproducible from a seed. They are
deliberately trivial (no real database) -- they exist to give the LangGraph flow
real evidence transitions that tracing can observe.

= Tool behaviors

``search_kb(query)``:
    Looks up ``query`` (normalized to lowercase) in a small hardcoded knowledge
    base.  Returns a hit (with ``ok=True``) when the query matches a known key;
    otherwise returns ``ok=False`` with ``message="no knowledge base match"``.

``lookup_account(account_id)``:
    Checks whether the ``account_id`` is in the ``MISSING_ACCOUNTS`` frozenset.
    Known accounts return ``ok=True``; missing accounts return ``ok=False``,
    which flips the healthy run path into a non-converging retry loop.

``infer_estimated_cost(turns, per_turn)``:
    Pure linear cost model: ``turns * per_turn``, rounded to 6 decimal places.
    This deterministic cost allows analytics tests to precisely verify cost
    calculations against known step counts.

= Seeded failure modes
The ``MISSING_ACCOUNTS`` set (containing ``acc_404`` and ``acc_500``) is the
mechanism that creates loop scenarios: any run seeded with one of these account
IDs will never converge because ``lookup_account`` keeps returning ``ok=False``,
which means the resolution gate in ``graph.py/run_tool`` never opens.

The ``KNOWLEDGE_BASE`` only contains three entries (reset password, refund policy,
billing cycle).  Any intent not matching these exact strings produces a miss from
``search_kb``, which also blocks resolution.
"""

from __future__ import annotations

from dataclasses import dataclass

# A request whose account_id is present in this set is treated as "missing" and
# forces the agent into a non-converging retry loop. ``acc_404`` and ``acc_500``
# are used by the ``loop`` seed in seeds.py.
# Using ``frozenset`` ensures this is immutable: no accidental mutation during
# concurrent test runs or graph replays.
MISSING_ACCOUNTS: frozenset[str] = frozenset({"acc_404", "acc_500"})

# A fixed, tiny knowledge base. A request whose intent is present here resolves.
# Keys are normalized lowercase phrases; ``search_kb`` lowercases its input so the
# lookup is case-insensitive.
# The knowledge base is deliberately small (3 entries) so most intents result in
# a miss -- this is realistic for a demo and makes the "loop" scenario easy to
# trigger.
KNOWLEDGE_BASE: dict[str, str] = {
    "reset password": "Use /account/reset and verify by email.",
    "refund policy": "Refunds are issued within 14 days of the order.",
    "billing cycle": "Invoices are generated on the 1st of each month.",
}


@dataclass(frozen=True)
class ToolResult:
    """A deterministic tool result.

    Frozen so results can be replayed/shared safely across graph invocations
    without risk of mutation.  ``hit`` carries the matched knowledge-base answer,
    ``account_id`` the subject account, ``ok`` the success flag, and ``message``
    a human label that shows in traces/tool logs.

    Attributes:
        hit: Matched knowledge-base answer text (knowledge-base lookups only).
            None when the tool doesn't match or isn't a KB search.
        account_id: The account that was looked up.  None for KB searches.
        ok: Whether the tool succeeded.  The resolution gate in ``graph.py``
            requires BOTH ``ok=True`` on account lookups AND ``hit is not None``
            on KB searches -- so a single failure blocks resolution.
        message: Short human-readable result label (e.g. "account found",
            "no knowledge base match").  Appears in trace logs.
    """

    hit: str | None = None
    account_id: str | None = None
    ok: bool = False
    message: str = ""


def search_kb(query: str) -> ToolResult:
    """Return a knowledge-base hit for ``query``, or ``None`` if unknown.

    Normalizes the query (strip + lowercase) to match ``KNOWLEDGE_BASE`` keys
    regardless of seed casing.  A miss returns ``ok=False`` with a clear
    message; this failure propagates through the graph as a resolution blocker.

    Args:
        query: The user's intent string (e.g. "reset password", "refund").

    Returns:
        A ``ToolResult`` with ``hit`` set to the KB answer text and ``ok=True``
        on match; ``ok=False`` with ``message="no knowledge base match"`` on miss.
    """
    token = query.strip().lower()
    hit = KNOWLEDGE_BASE.get(token)
    if hit is None:
        return ToolResult(message="no knowledge base match")
    return ToolResult(hit=hit, ok=True, message="knowledge base match")


def lookup_account(account_id: str) -> ToolResult:
    """Return account context, or a failed lookup for a missing account.

    The ``MISSING_ACCOUNTS`` set is what flips the healthy path into a retry loop:
    any id in the set returns ``ok=False`` (account not found).  This is the
    primary mechanism for creating loop scenarios in the demo agent.

    Args:
        account_id: The account identifier (e.g. "acc_100" for known,
            "acc_404" for missing).

    Returns:
        A ``ToolResult`` with ``ok=True`` and ``message="account found"`` for
        known accounts; ``ok=False`` with ``message="account {id} not found"``
        for missing accounts.
    """
    if account_id in MISSING_ACCOUNTS:
        return ToolResult(
            account_id=account_id, ok=False, message=f"account {account_id} not found"
        )
    return ToolResult(account_id=account_id, ok=True, message="account found")


def infer_estimated_cost(turns: int, per_turn: float) -> float:
    """Best-effort deterministic cost model: number of turns * per-turn cost.

    A pure, linear model so cost analytics can be validated precisely against the
    seeded step counts.  ``round(..., 6)`` keeps floats stable and readable in
    trace output and avoids floating-point noise in comparisons.

    Args:
        turns: Number of planner turns executed (from ``state["step"]``).
        per_turn: Fixed per-turn cost (``PER_TURN_COST = 0.01`` in graph.py).

    Returns:
        The estimated cost in USD, rounded to 6 decimal places.
    """
    return round(turns * per_turn, 6)

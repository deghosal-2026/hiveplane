"""Supersession-chain helpers (#544).

Turn the single ``superseded_by`` pointer on :class:`StandingRule` into
navigable v1→v2→v3 lineages: resolve chains (oldest → newest, cycle-guarded),
find chain tips, and surface dangling pointers / cycles / orphan middles for
``health``.
"""

from __future__ import annotations

from dataclasses import dataclass

from cauterule.models.rule import StandingRule

_MAX_CHAIN_DEPTH = 50


class ChainCycleError(ValueError):
    """Raised when a supersession cycle is detected."""


@dataclass(frozen=True)
class SupersessionIssue:
    """A health finding about the supersession structure."""

    kind: str  # "dangling" | "cycle" | "orphan_middle"
    rule_id: str
    detail: str

    def render(self) -> str:
        """Return a one-line render of this health issue."""
        return f"{self.kind}: {self.rule_id} — {self.detail}"


def _index_by_id(rules: list[StandingRule]) -> dict[str, StandingRule]:
    return {r.id: r for r in rules}


def chain(rule_id: str, rules: list[StandingRule]) -> list[StandingRule]:
    """Return the supersession chain for *rule_id*, oldest → newest.

    ``superseded_by`` points forward (this rule → its successor).  We walk
    backward to the chain root via a reverse map, then forward to the tip,
    cycle-guarded throughout (a cycle raises :class:`ChainCycleError`).

    Args:
        rule_id: Any rule in the chain (start point).
        rules: The full rule store.

    Returns:
        Ordered chain from the root rule to the tip (both inclusive).

    Raises:
        ChainCycleError: If following ``superseded_by`` links loops.
    """
    by_id = _index_by_id(rules)
    if rule_id not in by_id:
        return []

    # Reverse map: successor → the rule that superseded it.
    predecessor_of: dict[str, str] = {}
    for r in rules:
        nxt = r.superseded_by
        if nxt is not None:
            predecessor_of[nxt] = r.id

    # Walk backward from rule_id to the root (target of no pointer).
    root = rule_id
    seen_back: set[str] = set()
    while root in predecessor_of:
        if root in seen_back:
            msg = f"supersession cycle at {root!r}"
            raise ChainCycleError(msg)
        seen_back.add(root)
        root = predecessor_of[root]
        if len(seen_back) >= _MAX_CHAIN_DEPTH:
            msg = f"chain too deep (>{_MAX_CHAIN_DEPTH}) at {root!r}"
            raise ChainCycleError(msg)

    # Walk forward from the root following superseded_by to the tip.
    order: list[str] = []
    current = root
    seen_fwd: set[str] = set()
    while current in by_id:
        if current in seen_fwd:
            msg = f"supersession cycle at {current!r}"
            raise ChainCycleError(msg)
        seen_fwd.add(current)
        order.append(current)
        nxt = by_id[current].superseded_by
        if nxt is None:
            break
        current = nxt
        if len(order) >= _MAX_CHAIN_DEPTH:
            msg = f"chain too deep (>{_MAX_CHAIN_DEPTH}) at {current!r}"
            raise ChainCycleError(msg)

    return [by_id[cid] for cid in order]


def heads(rules: list[StandingRule]) -> list[StandingRule]:
    """Return chain tips — rules nobody supersedes (active/newest leads)."""
    by_id = _index_by_id(rules)
    heads_: list[StandingRule] = []
    for rule in rules:
        # A head is a rule with no successor pointer; orphaned holders of a
        # pointer are not tips. End of a supersession chain only.
        if rule.id in by_id and rule.superseded_by is None:
            heads_.append(rule)
    return heads_


def dangling(rules: list[StandingRule]) -> list[SupersessionIssue]:
    """Return dangling ``superseded_by`` targets (point to a missing rule)."""
    by_id = _index_by_id(rules)
    issues: list[SupersessionIssue] = []
    for rule in rules:
        nxt = rule.superseded_by
        if nxt is not None and nxt not in by_id:
            issues.append(
                SupersessionIssue(
                    kind="dangling",
                    rule_id=rule.id,
                    detail=f"superseded_by {nxt!r} not in store",
                )
            )
    return issues


def cycles(rules: list[StandingRule]) -> list[SupersessionIssue]:
    """Return any supersession cycles (A supersedes B supersedes A)."""
    issues: list[SupersessionIssue] = []
    checked: set[str] = set()
    by_id = _index_by_id(rules)
    for rule in rules:
        if rule.id in checked or not rule.superseded_by:
            continue
        seen: set[str] = set()
        current = rule.id
        while current in by_id and current not in seen:
            seen.add(current)
            current = by_id[current].superseded_by or ""
            if not current:
                break
        if current and current in seen:
            issues.append(
                SupersessionIssue(
                    kind="cycle",
                    rule_id=rule.id,
                    detail="supersession loop detected",
                )
            )
            checked.update(seen)
    return issues


def orphan_middles(rules: list[StandingRule]) -> list[SupersessionIssue]:
    """Return ``superseded`` status rules with no successor target."""
    issues: list[SupersessionIssue] = []
    for rule in rules:
        if rule.status == "superseded" and rule.superseded_by is None:
            issues.append(
                SupersessionIssue(
                    kind="orphan_middle",
                    rule_id=rule.id,
                    detail="status=superseded with no superseded_by pointer",
                )
            )
    return issues


def all_issues(rules: list[StandingRule]) -> list[SupersessionIssue]:
    """Return every supersession health issue (dangling + cycles + orphans)."""
    return dangling(rules) + cycles(rules) + orphan_middles(rules)


def render_chain(chain_rules: list[StandingRule]) -> str:
    """Render a chain as ``v1 <id> (status) → v2 <id> (status) → ...``."""
    parts: list[str] = []
    for r in chain_rules:
        parts.append(f"{r.id} ({r.status})")
    return " → ".join(parts)

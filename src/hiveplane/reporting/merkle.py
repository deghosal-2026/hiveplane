"""Merkle-tree integrity proofs over audit record hashes (M57-03).

A root is built by hashing each leaf value with :func:`leaf_hash`, then
pairwise-combining adjacent nodes bottom-up. An odd node at any level is
duplicated so every level pairs cleanly. Proof entries are direction-tagged
siblings (``"L:<hash>"`` when the sibling is the left child, ``"R:<hash>"``
when it is the right child) so :func:`verify_proof` needs no index.
"""

from __future__ import annotations

import hashlib

_NULL_ROOT = "0" * 64
_LEFT = "L"
_RIGHT = "R"


def leaf_hash(value: str) -> str:
    """Return the sha256 hex digest of ``value``."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _combine(left: str, right: str) -> str:
    return hashlib.sha256((left + right).encode("utf-8")).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    """Return the Merkle root over ``leaves`` (64 zeros when empty)."""
    if not leaves:
        return _NULL_ROOT
    level = [leaf_hash(value) for value in leaves]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_combine(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves: list[str], index: int) -> list[str]:
    """Return the direction-tagged sibling path from ``leaves[index]`` to the root."""
    if index < 0 or index >= len(leaves):
        raise IndexError(f"leaf index out of range: {index}")
    level = [leaf_hash(value) for value in leaves]
    proof: list[str] = []
    position = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sibling = position ^ 1
        side = _LEFT if sibling < position else _RIGHT
        proof.append(f"{side}:{level[sibling]}")
        level = [_combine(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        position //= 2
    return proof


def verify_proof(leaf: str, proof: list[str], root: str) -> bool:
    """Return True when ``proof`` shows ``leaf`` folds into ``root``."""
    current = leaf_hash(leaf)
    for step in proof:
        side, separator, sibling = step.partition(":")
        if not separator:
            return False
        if side == _LEFT:
            current = _combine(sibling, current)
        elif side == _RIGHT:
            current = _combine(current, sibling)
        else:
            return False
    return current == root

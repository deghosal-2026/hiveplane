"""Unit tests for the Merkle integrity proof (M57-03)."""

from __future__ import annotations

import hashlib

import pytest

from hiveplane.reporting.merkle import (
    leaf_hash,
    merkle_proof,
    merkle_root,
    verify_proof,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _combine(left: str, right: str) -> str:
    return _hash(left + right)


def _reference_root(values: list[str]) -> str:
    if not values:
        return "0" * 64
    level = [_hash(value) for value in values]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_combine(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def test_leaf_hash_is_sha256_hex() -> None:
    assert leaf_hash("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(leaf_hash("")) == 64


def test_empty_root_is_zeros() -> None:
    assert merkle_root([]) == "0" * 64


def test_single_leaf_root_is_leaf_hash() -> None:
    assert merkle_root(["only"]) == leaf_hash("only")


@pytest.mark.parametrize(
    "leaves",
    [
        ["a"],
        ["a", "b"],
        ["a", "b", "c"],
        ["a", "b", "c", "d"],
        ["r0", "r1", "r2", "r3", "r4"],
    ],
)
def test_known_leaf_sets_produce_stable_roots(leaves: list[str]) -> None:
    assert merkle_root(leaves) == _reference_root(leaves)
    assert merkle_root(leaves) == merkle_root(list(leaves))


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6, 7, 8])
def test_proof_verifies_for_every_index(size: int) -> None:
    leaves = [f"leaf-{i}" for i in range(size)]
    root = merkle_root(leaves)
    for index in range(size):
        proof = merkle_proof(leaves, index)
        assert verify_proof(leaves[index], proof, root) is True


@pytest.mark.parametrize("size", [2, 3, 4, 5])
def test_proof_rejects_wrong_leaf(size: int) -> None:
    leaves = [f"leaf-{i}" for i in range(size)]
    root = merkle_root(leaves)
    proof = merkle_proof(leaves, 0)
    assert verify_proof("tampered", proof, root) is False


def test_proof_rejects_wrong_root() -> None:
    leaves = ["a", "b", "c"]
    proof = merkle_proof(leaves, 1)
    assert verify_proof("b", proof, merkle_root(["a", "b", "d"])) is False


def test_proof_rejects_tampered_proof() -> None:
    leaves = ["a", "b", "c", "d"]
    root = merkle_root(leaves)
    proof = merkle_proof(leaves, 2)
    tampered = list(proof)
    tampered[0] = "R:" + ("0" * 64)
    assert verify_proof("c", tampered, root) is False


def test_proof_index_out_of_range() -> None:
    with pytest.raises(IndexError):
        merkle_proof(["a", "b"], 2)
    with pytest.raises(IndexError):
        merkle_proof([], 0)


def test_verify_rejects_malformed_proof_entry() -> None:
    root = merkle_root(["a", "b"])
    assert verify_proof("a", ["deadbeef"], root) is False
    assert verify_proof("a", ["X:" + (root)], root) is False

"""Tests for fleet bundle export/import with provenance (M54-05/06)."""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hiveplane.artifacts.export import (
    apply_import,
    export_fleet_bundle,
    import_fleet_bundle,
    parse_fleet_bundle,
    plan_import,
    verify_fleet_bundle,
)
from hiveplane.transparency.errors import BundleVerificationError

_WORKLOAD = {
    "apiVersion": "hiveplane/v1",
    "kind": "AgentWorkload",
    "metadata": {"name": "summarizer", "owner": "platform", "team": "platform"},
    "spec": {"runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"}},
}
_CORPUS = {"id": "corpora/summarizer/v1", "version": 1, "tasks": []}
_POLICY = {
    "apiVersion": "hiveplane/v1",
    "kind": "PolicyPack",
    "metadata": {"name": "team-pack", "team": "platform", "version": "1"},
    "spec": {},
}


def _keypair() -> tuple[Ed25519PrivateKey, Any]:
    private = Ed25519PrivateKey.generate()
    return private, private.public_key()


def test_signed_bundle_round_trips_and_verifies() -> None:
    private, public = _keypair()
    bundle = export_fleet_bundle(
        workload=_WORKLOAD, corpus=_CORPUS, policy_pack=_POLICY,
        private_key=private, key_id="k1",
    )

    assert bundle.provenance is not None
    assert bundle.provenance.key_id == "k1"
    assert verify_fleet_bundle(bundle, public) is True

    imported = import_fleet_bundle(bundle.model_dump(), public)
    assert imported.workload == _WORKLOAD
    assert imported.corpus == _CORPUS
    assert imported.policy_pack == _POLICY


def test_unsigned_bundle_has_no_provenance() -> None:
    bundle = export_fleet_bundle(workload=_WORKLOAD)
    assert bundle.provenance is None


def test_tampered_bundle_is_refused() -> None:
    private, public = _keypair()
    bundle = export_fleet_bundle(workload=_WORKLOAD, private_key=private)
    document = bundle.model_dump()
    document["workload"]["metadata"]["name"] = "evil"

    assert verify_fleet_bundle(parse_fleet_bundle(document), public) is False
    with pytest.raises(BundleVerificationError):
        import_fleet_bundle(document, public)


def test_importing_unsigned_bundle_requires_explicit_allow() -> None:
    bundle = export_fleet_bundle(workload=_WORKLOAD)
    with pytest.raises(BundleVerificationError):
        import_fleet_bundle(bundle.model_dump())
    assert import_fleet_bundle(bundle.model_dump(), allow_unsigned=True).workload == _WORKLOAD


def test_import_rejects_malformed_document() -> None:
    with pytest.raises(BundleVerificationError):
        parse_fleet_bundle({"nope": True})


def test_wrong_kind_is_rejected() -> None:
    with pytest.raises(BundleVerificationError):
        parse_fleet_bundle({"apiVersion": "hiveplane/export/v1", "kind": "Other"})


def test_plan_import_marks_create_and_update() -> None:
    bundle = export_fleet_bundle(workload=_WORKLOAD, corpus=_CORPUS)

    plan = plan_import(bundle, existing=lambda kind, name: name == "summarizer")

    actions = {(entry.kind, entry.name): entry.action for entry in plan.entries}
    assert actions[("workload", "summarizer")] == "update"
    assert actions[("corpus", "corpora/summarizer/v1")] == "create"
    assert plan.dry_run is True


def test_dry_run_import_writes_nothing() -> None:
    bundle = export_fleet_bundle(workload=_WORKLOAD)
    plan = plan_import(bundle)
    applied: list[str] = []

    result = apply_import(
        bundle,
        plan,
        handlers={"workload": lambda document: applied.append("workload")},
        dry_run=True,
    )

    assert applied == []
    assert result.dry_run is True


def test_applying_import_calls_handlers() -> None:
    bundle = export_fleet_bundle(workload=_WORKLOAD, corpus=_CORPUS)
    plan = plan_import(bundle)
    applied: list[str] = []

    result = apply_import(
        bundle,
        plan,
        handlers={
            "workload": lambda document: applied.append("workload"),
            "corpus": lambda document: applied.append("corpus"),
        },
        dry_run=False,
    )

    assert applied == ["workload", "corpus"]
    assert result.dry_run is False

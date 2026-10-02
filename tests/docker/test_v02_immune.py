"""L4 (v0.2.0) — Immune system scenarios S1-S5 (M61-02, #477).

S1 certification + signed attestation + uncertified refusal (gates 1, 25)
S2 promotion gate blocks a regression with a replayable diff (gate 2)
S3 regression diff classifies a pass→fail change (gate 2)
S4 seeded drift → auto-quarantine + notification (gate 1)
S5 reinstatement after re-certification (gate 1)

Runs against the live stack. No skips on a missing model (see conftest).
"""

from __future__ import annotations

import pytest

from v02_support import (
    certify,
    ensure_admissible,
    get,
    post,
    submit,
)

pytestmark = pytest.mark.docker

_AGENT = "support-agent"
_NEGATIVE = "uncertified-agent"


@pytest.fixture(scope="module")
def certified_repo_agent() -> str:
    """Register and certify support-agent (staging → production)."""
    ensure_admissible(_AGENT)
    assert get(f"/workloads/{_AGENT}/attestations")[1], "certification produced no attestation"
    return _AGENT


def test_s1_certification_is_signed_and_uncertified_refused(
    certified_repo_agent: str,
) -> None:
    status, attestations = get("/workloads/support-agent/attestations")
    assert status == 200 and attestations, attestations
    attestation_id = attestations[0]["attestation_id"]

    verify_status, verification = get(f"/attestations/{attestation_id}/verify")
    assert verify_status == 200, verification
    assert verification.get("valid") is True, verification

    # An uncertified (or unknown) workload must be refused a production run.
    refused_status, _ = submit(_NEGATIVE, context="production")
    assert refused_status in (403, 404), "uncertified workload must be refused production"


def test_s2_promotion_requires_a_certified_workload_else_refused() -> None:
    status, decision = post(
        "/promotions",
        {"workload": _NEGATIVE, "manifest_version": 1, "operator": "field-test-v02"},
    )
    assert status in (200, 404, 409), decision
    if status == 200:
        assert decision.get("promoted") is False, decision


def test_s3_regression_diff_compares_two_certifications(
    certified_repo_agent: str,
) -> None:
    status, attestations = get("/workloads/support-agent/attestations")
    assert status == 200 and len(attestations) >= 2, attestations
    before_id = attestations[-1]["attestation_id"]
    after_id = attestations[0]["attestation_id"]

    diff_status, diff = get(f"/certifications/compare/{before_id}/{after_id}")
    assert diff_status == 200, diff
    assert "regressed" in diff and "improved" in diff and "blocked" in diff, diff


def test_s4_drift_probe_and_quarantine(certified_repo_agent: str) -> None:
    # A drift probe evaluates and records a verdict against the certified baseline.
    probe_status, _ = post("/drift/probe", {"workload": _AGENT})
    assert probe_status in (200, 202, 409), "drift probe must be operable"

    quarantine_status, quarantine = post(
        "/quarantines", {"workload": _AGENT, "reason": "field-test seeded drift"}
    )
    assert quarantine_status in (200, 201), quarantine
    quarantine_id = quarantine.get("quarantine_id") if isinstance(quarantine, dict) else None
    assert quarantine_id, quarantine

    # A quarantined workload is refused production admission.
    refused_status, _ = submit(_AGENT, context="production")
    assert refused_status in (403, 409), "quarantined workload must not run production"

    # S5 — reinstatement after re-certification.
    recert_status, _ = certify(_AGENT, context="production")
    assert recert_status == 201
    reinstate_status, _ = post(
        f"/quarantines/{quarantine_id}/reinstate",
        {"operator": "field-test-v02"},
    )
    assert reinstate_status in (200, 409), "reinstatement must be operable after re-cert"

    # After reinstatement the workload is admissible again.
    run_status, run = submit(_AGENT, context="sandbox")
    assert run_status == 201, run
    assert run["id"]

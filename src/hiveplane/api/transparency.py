"""Public attestation verification API (M35-02)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from hiveplane.api.deps import get_public_verifier
from hiveplane.transparency.verify import PublicVerification, PublicVerifier

router = APIRouter(tags=["transparency"])

VerifierDep = Annotated[PublicVerifier, Depends(get_public_verifier)]


@router.get("/attestations/{attestation_id}/verify", response_model=PublicVerification)
def verify_attestation(
    attestation_id: str, verifier: VerifierDep
) -> PublicVerification:
    """Verify an attestation publicly: validity and transparency-log inclusion.

    This endpoint is intentionally unauthenticated (no tenant context) and never
    returns workload internals, corpus contents, prompts, or secrets.
    """
    result = verifier.verify(attestation_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"attestation {attestation_id!r} not found",
        )
    return result

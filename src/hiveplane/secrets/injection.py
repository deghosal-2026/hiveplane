"""Resolve secrets at the execution boundary and inject them (M45-02)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.secrets.models import SecretInjection, SecretInjectionKind, SecretRef
from hiveplane.secrets.redaction import RedactorRegistry
from hiveplane.secrets.service import SecretService


class InjectedSecret(BaseModel):
    """A secret resolved for a run, delivered as env vars or tmpfs files."""

    model_config = ConfigDict(extra="forbid")

    ref: SecretRef
    env: dict[str, str] = Field(default_factory=dict, repr=False)
    files: dict[str, str] = Field(default_factory=dict, repr=False)
    mode: str = "0400"


class SecretInjector:
    """Resolves and injects secrets at the boundary, redacting them for the run."""

    def __init__(self, service: SecretService, redactors: RedactorRegistry) -> None:
        self._service = service
        self._redactors = redactors

    def inject(
        self,
        ref: SecretRef,
        injection: SecretInjection | None,
        *,
        run_id: str,
        workload: str | None = None,
    ) -> InjectedSecret:
        """Resolve a ref and register its value with the run's redactor."""
        resolved = self._service.resolve(ref, run_id=run_id, workload=workload)
        self._redactors.for_run(run_id).register(resolved.value)
        if injection is not None and injection.injection_kind is SecretInjectionKind.FILE:
            path = injection.path or f"/run/secrets/{ref.name}"
            return InjectedSecret(
                ref=resolved.ref, files={path: resolved.value}, mode=injection.mode
            )
        name = injection.name if injection is not None and injection.name else ref.name
        return InjectedSecret(ref=resolved.ref, env={name: resolved.value})

    def release(self, run_id: str) -> None:
        """Release a run's redactor when the run finishes."""
        self._redactors.release(run_id)

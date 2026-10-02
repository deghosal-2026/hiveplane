"""Resolve secrets at the execution boundary and inject them (M45-02)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.secrets.models import (
    SecretInjection,
    SecretInjectionKind,
    SecretRef,
    SecretResolutionError,
)
from hiveplane.secrets.redaction import Redactor, RedactorRegistry
from hiveplane.secrets.service import SecretService
from hiveplane.tenancy import TenantContext

if TYPE_CHECKING:
    from hiveplane.core.spec import SecretBinding
    from hiveplane.core.workload import AgentWorkload


class InjectedSecret(BaseModel):
    """A secret resolved for a run, delivered as env vars or tmpfs files."""

    model_config = ConfigDict(extra="forbid")

    ref: SecretRef
    env: dict[str, str] = Field(default_factory=dict, repr=False)
    files: dict[str, str] = Field(default_factory=dict, repr=False)
    mode: str = "0400"


class SecretInjector:
    """Resolves and injects secrets at the boundary, redacting them for the run."""

    def __init__(
        self,
        service: SecretService,
        redactors: RedactorRegistry,
        *,
        sink_redactor: Redactor | None = None,
    ) -> None:
        self._service = service
        self._redactors = redactors
        self._sink_redactor = sink_redactor

    def inject(
        self,
        ref: SecretRef,
        injection: SecretInjection | None,
        *,
        run_id: str,
        workload: str | None = None,
        ctx: TenantContext | None = None,
        allowed_refs: Iterable[str] | None = None,
    ) -> InjectedSecret:
        """Resolve a ref and register its value with the run's redactor.

        ``ctx`` (the run's trusted context) must scope the ref's tenant, and an
        ``allowed_refs`` allow-list (the manifest's declared refs) must contain
        the ref, so a workload cannot resolve arbitrary secrets.
        """
        rendered = ref.render()
        if allowed_refs is not None and rendered not in set(allowed_refs):
            raise SecretResolutionError(rendered, "ref is not declared by the workload")
        resolved = self._service.resolve(
            ref, run_id=run_id, workload=workload, ctx=ctx
        )
        self._redactors.for_run(run_id).register(resolved.value)
        if self._sink_redactor is not None:
            self._sink_redactor.register(resolved.value)
        if injection is not None and injection.injection_kind is SecretInjectionKind.FILE:
            path = injection.path or f"/run/secrets/{ref.name}"
            return InjectedSecret(
                ref=resolved.ref, files={path: resolved.value}, mode=injection.mode
            )
        name = injection.name if injection is not None and injection.name else ref.name
        return InjectedSecret(ref=resolved.ref, env={name: resolved.value})

    def inject_for_workload(
        self,
        manifest: AgentWorkload,
        *,
        run_id: str,
        ctx: TenantContext,
    ) -> dict[str, str]:
        """Inject every manifest-declared secret into a run's environment.

        Each binding must name the run's own tenant; the manifest is the
        allow-list, so a workload can only resolve secrets it declares.
        """
        bindings: list[SecretBinding] = manifest.spec.secrets
        allowed = [binding.ref for binding in bindings]
        env: dict[str, str] = {}
        for binding in bindings:
            injected = self.inject(
                binding.parsed(),
                binding.injection_spec(),
                run_id=run_id,
                workload=manifest.name,
                ctx=ctx,
                allowed_refs=allowed,
            )
            env.update(injected.env)
        return env

    def release(self, run_id: str) -> None:
        """Release a run's redactor when the run finishes."""
        self._redactors.release(run_id)

"""Team policy packs: versioning, inheritance, linting, and application (M40-04).

Packs may tighten but never loosen; a parent pack's rules apply before its
children. Lint validates schema (via the model), unknown parents, and inheritance
cycles. Publish is immutable (a name cannot be re-saved with different content);
apply pins a resolved pack chain to a team.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.policy.models import PolicyPack
from hiveplane.policy.packs import PolicyPackStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class PackLintResult(BaseModel):
    """The result of linting a policy pack."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: list[str] = Field(default_factory=list)


class PolicyPackRegistry:
    """Publishes, lints, resolves, and applies team policy packs."""

    def __init__(
        self, store: PolicyPackStore, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def lint(
        self, pack: PolicyPack, *, available: dict[str, PolicyPack] | None = None
    ) -> PackLintResult:
        """Validate a pack's inheritance references and detect cycles."""
        catalog = available or {}
        issues: list[str] = []
        for parent in pack.spec.inherits:
            if parent not in catalog:
                issues.append(f"inherits unknown pack {parent!r}")
        if self._has_cycle(pack, catalog, (pack.metadata.name,)):
            issues.append("inheritance cycle detected")
        return PackLintResult(valid=not issues, issues=issues)

    def publish(self, pack: PolicyPack, *, ctx: TenantContext = DEFAULT_CONTEXT) -> PolicyPack:
        """Publish an immutable pack version (rejects a duplicate name)."""
        self._store.save(pack, ctx=ctx)
        return pack

    def resolve(
        self, pack: PolicyPack, *, available: dict[str, PolicyPack]
    ) -> list[PolicyPack]:
        """Return the inheritance chain for a pack, parents first."""
        chain: list[PolicyPack] = []
        for parent_name in pack.spec.inherits:
            parent = available.get(parent_name)
            if parent is not None:
                chain.extend(self.resolve(parent, available=available))
        chain.append(pack)
        return chain

    def apply(
        self,
        name: str,
        *,
        team: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[PolicyPack]:
        """Pin the resolved chain of a published pack to a team, in order."""
        catalog = {
            pack.metadata.name: pack for pack in self._store.list_packs(ctx=ctx)
        }
        root = catalog.get(name)
        if root is None:
            raise KeyError(name)
        applied: list[PolicyPack] = []
        for pack in self.resolve(root, available=catalog):
            pinned = pack.model_copy(
                update={
                    "metadata": pack.metadata.model_copy(
                        update={"team": team, "name": f"{team}:{pack.metadata.name}"}
                    ),
                    "spec": pack.spec.model_copy(update={"inherits": []}),
                }
            )
            self._store.save(pinned, ctx=ctx)
            applied.append(pinned)
        return applied

    def _has_cycle(
        self, pack: PolicyPack, catalog: dict[str, PolicyPack], seen: tuple[str, ...]
    ) -> bool:
        for parent_name in pack.spec.inherits:
            if parent_name in seen:
                return True
            parent = catalog.get(parent_name)
            if parent is not None and self._has_cycle(
                parent, catalog, (*seen, parent_name)
            ):
                return True
        return False

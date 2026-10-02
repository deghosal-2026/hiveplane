"""Read-only live-state readers the `ask` copilot depends on (M53)."""

from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue


class AskReaders(Protocol):
    """A strictly read-only, tenant-scoped view of live control-plane state.

    There is deliberately no mutation method: the copilot cannot change state by
    construction (M53-04). Every method takes the caller's ``tenant_id`` so an
    operator can only read their own tenant's data.
    """

    def run(self, run_id: str, *, tenant_id: str) -> dict[str, JsonValue]: ...

    def team_spend(
        self, team: str, period: str, *, tenant_id: str
    ) -> dict[str, JsonValue]: ...

    def approval(self, approval_id: str, *, tenant_id: str) -> dict[str, JsonValue]: ...

    def health(self, workload: str, *, tenant_id: str) -> dict[str, JsonValue]: ...

    def fleet(self, *, tenant_id: str) -> dict[str, JsonValue]: ...

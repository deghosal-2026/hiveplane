"""UI RBAC: which actions an operator role may see and perform (M52-01).

The UI hides controls for usability, but the server is the gate: this module is
the single source for both the rendered controls and the route checks.
"""

from __future__ import annotations

from typing import NamedTuple

from hiveplane.auth.models import AuthMethod, Permission
from hiveplane.auth.rbac import has_permission
from hiveplane.tenancy.models import Role


class UiIdentity(NamedTuple):
    """The caller's role, used to gate UI controls."""

    role: Role
    operator_id: str = "anonymous"


def visible_actions(identity: UiIdentity) -> dict[str, bool]:
    """Return which privileged controls the role may see/perform."""
    return {
        "approve": can(identity, Permission.APPROVE),
        "promote": can(identity, Permission.PROMOTE),
        "kill_switch": can(identity, Permission.KILL_SWITCH),
        "intervene": can(identity, Permission.RUN_INTERVENE),
        "secrets_manage": can(identity, Permission.SECRETS_MANAGE),
    }


def can(identity: UiIdentity, permission: Permission) -> bool:
    """Return True when the identity's role grants the permission."""
    from hiveplane.auth.models import OperatorIdentity
    from hiveplane.tenancy.context import DEFAULT_TENANT_ID

    return has_permission(
        OperatorIdentity(
            operator_id=identity.operator_id,
            tenant_id=DEFAULT_TENANT_ID,
            role=identity.role,
            method=AuthMethod.SESSION,
        ),
        permission,
    )

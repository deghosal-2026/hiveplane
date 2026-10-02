"""Deny-by-default egress policy with ports and wildcards (M39-05, M39-06).

Enforced at the control-plane boundary (sandbox channel / tool-call boundary).
Production runs additionally constrain egress at the network namespace or CNI
layer; this policy is the authoritative allow-list those layers mirror.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from hiveplane.core.sandbox import (
    CLOUD_METADATA_ENDPOINTS,
    EgressMode,
    EgressRule,
    SandboxSpec,
)


class EgressDecision(BaseModel):
    """The boundary's egress verdict with a reason and denying rule id."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: str
    rule: str | None = None


class EgressPolicy:
    """Evaluates an outbound host/port against a deny-by-default allow-list."""

    def __init__(
        self,
        *,
        mode: EgressMode = EgressMode.RESTRICTED,
        allow: list[EgressRule] | None = None,
        deny: list[EgressRule] | None = None,
    ) -> None:
        self._mode = mode
        self._allow = list(allow or [])
        self._deny = list(deny or [])

    @classmethod
    def from_sandbox(cls, sandbox: SandboxSpec | None) -> EgressPolicy:
        """Build the policy for a workload, preferring ``sandbox.network``."""
        if sandbox is None:
            return cls()
        if sandbox.network is not None:
            return cls(
                mode=sandbox.network.egress,
                allow=sandbox.network.allow,
                deny=sandbox.network.deny,
            )
        return cls(
            mode=sandbox.egress.mode,
            allow=[EgressRule(host=host) for host in sandbox.egress.allow],
            deny=[EgressRule(host=host) for host in sandbox.egress.deny],
        )

    def check(self, host: str, port: int | None = None) -> EgressDecision:
        """Decide whether an outbound host/port is permitted."""
        normalized = host.lower()
        if normalized in CLOUD_METADATA_ENDPOINTS:
            return _denied(f"egress to {host!r} is blocked (cloud metadata)", "egress.denied")
        if any(_rule_matches(rule, normalized, port) for rule in self._deny):
            return _denied(f"egress to {host!r} is explicitly denied", "egress.denied")
        if self._mode is EgressMode.NONE:
            return _denied("egress is disabled for this workload", "egress.none")
        if self._mode is EgressMode.OPEN:
            return EgressDecision(allowed=True, reason="egress.open", rule="egress.open")
        host_rules = [rule for rule in self._allow if _host_matches(rule.host, normalized)]
        if not host_rules:
            return _denied(f"egress to {host!r} is not on the allow-list", "egress.default_deny")
        if any(_port_matches(rule.port, port) for rule in host_rules):
            return EgressDecision(
                allowed=True,
                reason=f"egress to {host!r} is allow-listed",
                rule="egress.allow",
            )
        return _denied(f"egress to {host!r} is not permitted on this port", "egress.denied")


def _denied(reason: str, rule: str) -> EgressDecision:
    return EgressDecision(allowed=False, reason=reason, rule=rule)


def _rule_matches(rule: EgressRule, host: str, port: int | None) -> bool:
    return _host_matches(rule.host, host) and _port_matches(rule.port, port)


def _host_matches(pattern: str, host: str) -> bool:
    candidate = pattern.lower()
    if candidate.startswith("*."):
        suffix = candidate[2:]
        return host.endswith("." + suffix)
    return candidate == host


def _port_matches(rule_port: int | None, port: int | None) -> bool:
    if rule_port is None:
        return True
    return rule_port == port

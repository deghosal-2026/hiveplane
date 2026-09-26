"""Per-workload egress allow-lists with ports and wildcards (M39-05)."""

from __future__ import annotations

from hiveplane.core.sandbox import EgressMode, EgressRule, EgressSpec, NetworkSpec, SandboxSpec
from hiveplane.defense.egress import EgressPolicy


def _policy(*, mode: EgressMode = EgressMode.RESTRICTED, allow: list[EgressRule]) -> EgressPolicy:
    return EgressPolicy(mode=mode, allow=allow)


def test_default_deny_when_no_allow_rules() -> None:
    decision = _policy(allow=[]).check("api.openai.com", 443)
    assert decision.allowed is False
    assert decision.rule == "egress.default_deny"


def test_allowed_host_and_port() -> None:
    policy = _policy(allow=[EgressRule(host="api.openai.com", port=443)])
    assert policy.check("api.openai.com", 443).allowed is True


def test_port_mismatch_is_denied() -> None:
    policy = _policy(allow=[EgressRule(host="api.openai.com", port=443)])
    decision = policy.check("api.openai.com", 80)
    assert decision.allowed is False
    assert decision.rule == "egress.denied"


def test_any_port_rule_allows_any_port() -> None:
    policy = _policy(allow=[EgressRule(host="internal-metrics.corp.local")])
    assert policy.check("internal-metrics.corp.local", 9000).allowed is True


def test_wildcard_matches_subdomain() -> None:
    policy = _policy(allow=[EgressRule(host="*.corp.local", port=443)])
    assert policy.check("metrics.corp.local", 443).allowed is True


def test_wildcard_does_not_match_apex() -> None:
    policy = _policy(allow=[EgressRule(host="*.corp.local", port=443)])
    assert policy.check("corp.local", 443).allowed is False


def test_host_match_is_case_insensitive() -> None:
    policy = _policy(allow=[EgressRule(host="API.OpenAI.com", port=443)])
    assert policy.check("api.openai.com", 443).allowed is True


def test_explicit_deny_overrides_allow() -> None:
    policy = EgressPolicy(
        allow=[EgressRule(host="evil.example", port=443)],
        deny=[EgressRule(host="evil.example")],
    )
    decision = policy.check("evil.example", 443)
    assert decision.allowed is False
    assert decision.rule == "egress.denied"


def test_cloud_metadata_is_always_denied() -> None:
    policy = _policy(allow=[EgressRule(host="169.254.169.254", port=80)])
    assert policy.check("169.254.169.254", 80).allowed is False


def test_open_mode_allows_unknown_host() -> None:
    policy = _policy(mode=EgressMode.OPEN, allow=[])
    assert policy.check("anything.example", 443).allowed is True


def test_none_mode_denies_all() -> None:
    policy = _policy(mode=EgressMode.NONE, allow=[EgressRule(host="api.openai.com", port=443)])
    decision = policy.check("api.openai.com", 443)
    assert decision.allowed is False
    assert decision.rule == "egress.none"


def test_legacy_egress_spec_is_honored() -> None:
    sandbox = SandboxSpec(enabled=False, egress=EgressSpec(allow=["api.openai.com"]))
    policy = EgressPolicy.from_sandbox(sandbox)
    assert policy.check("api.openai.com").allowed is True
    assert policy.check("other.example").allowed is False


def test_network_spec_takes_precedence_over_legacy_egress() -> None:
    sandbox = SandboxSpec(
        enabled=False,
        egress=EgressSpec(allow=["legacy.example"]),
        network=NetworkSpec(allow=[EgressRule(host="api.openai.com", port=443)]),
    )
    policy = EgressPolicy.from_sandbox(sandbox)
    assert policy.check("api.openai.com", 443).allowed is True
    assert policy.check("legacy.example").allowed is False


def test_from_sandbox_defaults_to_deny_without_config() -> None:
    decision = EgressPolicy.from_sandbox(None).check("api.openai.com", 443)
    assert decision.allowed is False

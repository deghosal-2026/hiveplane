from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from planner_critic.reason_codes import POSTURE_RESOLVED
from planner_critic.schema.goal import RiskTolerance

logger = logging.getLogger(__name__)

_CONTEXT_SOURCES: list[tuple[str, Callable[[], str | None]]] = []


@dataclass(frozen=True)
class PostureRule:
    match: dict[str, str]
    posture: RiskTolerance


@dataclass(frozen=True)
class ResolvedPosture:
    posture: RiskTolerance
    rule_id: int | None
    context_signal: str | None

    @property
    def reason_code(self) -> str:
        return POSTURE_RESOLVED


class PostureResolver:
    def __init__(self, rules: list[PostureRule] | None = None) -> None:
        self._rules = rules or []

    @classmethod
    def from_dict(cls, data: list[dict[str, Any]]) -> PostureResolver:
        rules: list[PostureRule] = []
        for entry in data:
            match = dict(entry["match"])
            posture = RiskTolerance(entry["posture"])
            rules.append(PostureRule(match=match, posture=posture))
        return cls(rules)

    @classmethod
    def from_yaml(cls, path: str) -> PostureResolver:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data["posture_rules"])

    def resolve(self, goal_posture: RiskTolerance) -> ResolvedPosture:
        context = _collect_context()
        for i, rule in enumerate(self._rules):
            if _matches(rule.match, context):
                logger.info(
                    "posture: rule %d matched %r → %s",
                    i,
                    _matched_signal(rule.match, context),
                    rule.posture.value,
                )
                return ResolvedPosture(
                    posture=rule.posture,
                    rule_id=i,
                    context_signal=_matched_signal(rule.match, context),
                )
        logger.info("posture: no rule matched — fallback to goal posture %s", goal_posture.value)
        return ResolvedPosture(posture=goal_posture, rule_id=None, context_signal=None)


def _collect_context() -> dict[str, str]:
    ctx: dict[str, str] = {}
    ctx["env"] = os.environ.get("ENV", "")
    ctx["pc_env"] = os.environ.get("PC_ENV", "")
    branch = os.environ.get("PC_GIT_BRANCH", "")
    if not branch:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607  # intentional PATH lookup
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                branch = result.stdout.strip()
        except Exception:  # noqa: S110  # git absence must not break posture resolution
            pass
    ctx["git_branch"] = branch
    workspace = os.environ.get("PC_TERRAFORM_WORKSPACE", "")
    ctx["deploy_target"] = workspace
    namespace = os.environ.get("PC_K8S_NAMESPACE", "")
    ctx["k8s_namespace"] = namespace
    for key, func in _CONTEXT_SOURCES:
        try:
            val = func()
            if val is not None:
                ctx[key] = val
        except Exception:  # noqa: S110  # optional context probes are best-effort
            pass
    return ctx


def _matches(rule_match: dict[str, str], context: dict[str, str]) -> bool:
    for key, expected in rule_match.items():
        actual = context.get(key, "")
        if isinstance(expected, str) and expected.startswith("re:"):
            pattern = expected[3:]
            if not re.search(pattern, actual):
                return False
        elif actual != expected:
            return False
    return True


def _matched_signal(rule_match: dict[str, str], context: dict[str, str]) -> str:
    for key in rule_match:
        val = context.get(key, "")
        if val:
            return f"{key}={val}"
    return ""


def register_context_source(name: str, func: Callable[[], str | None]) -> None:
    _CONTEXT_SOURCES.append((name, func))

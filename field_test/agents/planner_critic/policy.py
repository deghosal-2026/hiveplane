"""Policy-as-Code engine (M3, #129) — OPA/Rego and CEL deterministic gates.

Provides an external deterministic gate layer that runs *in addition* to
the built-in Python gates. Two evaluators:

* :class:`RegoGate` — shell out to ``opa eval`` for full OPA/Rego policies.
* :class:`CelGate` — pure-Python expression evaluator for inline
  conditions without an external binary.

Both evaluators conform to the :class:`PolicyEngine` protocol so consumers
can swap or compose them at will.
"""

from __future__ import annotations

import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .reason_codes import POLICY_VIOLATION
from .schema.plan import PlanVersion
from .types import Finding, Severity

if TYPE_CHECKING:
    pass


class PolicyEngine(ABC):
    """Abstract policy evaluator.

    Subclasses implement :meth:`evaluate` which returns zero or more
    :class:`Finding` objects. An empty list means the policy passed.
    """

    name: str
    severity: Severity
    message: str | None
    reason_code: str | None

    def __init__(
        self,
        name: str,
        severity: str = "blocker",
        message: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        self.name = name
        self.severity = Severity(severity)
        self.message = message
        self.reason_code = reason_code

    @abstractmethod
    def evaluate(self, plan: PlanVersion) -> list[Finding]:
        """Evaluate this policy against a plan.

        Args:
            plan: The typed plan to evaluate.

        Returns:
            Findings describing every violation. Empty when the policy passes.
        """
        ...

    def _finding(self, plan: PlanVersion, detail: str = "") -> Finding:
        """Build a finding for this gate."""
        return Finding(
            id=f"policy:{self.name}:{plan.id}:{plan.version}",
            version=plan.version,
            severity=self.severity,
            reason_code=POLICY_VIOLATION,
            message=self.message or detail or f"policy violation: {self.name}",
        )


# ── CEL Gate ──────────────────────────────────────────────────────────────


def _safe_eval(expression: str, plan: PlanVersion) -> bool:
    """Evaluate a CEL-style expression against a plan in a restricted scope.

    The expression has access to:
    * ``tasks`` — list of task dicts (``{id, description, action, target,
      risk_class, blast_radius, parallel_group, verification, rollback}``)
    * ``dependencies`` — list of dependency dicts
    * ``branches`` — list of branch dicts
    * ``len()``, ``any()``, ``all()``, basic Python operators

    Args:
        expression: A Python expression string evaluated in a restricted env.
        plan: The plan to evaluate against.

    Returns:
        True when the expression holds (passes), False when it fails.
    """
    tasks_data = [t.model_dump() for t in plan.tasks]
    deps_data = [d.model_dump() for d in plan.dependencies]
    branches_data = [b.model_dump() for b in plan.branches]
    allowed_builtins: dict[str, Any] = {
        "len": len,
        "any": any,
        "all": all,
        "True": True,
        "False": False,
        "None": None,
        "sorted": sorted,
        "set": set,
        "list": list,
        "dict": dict,
        "str": str,
        "int": int,
        "float": float,
        "min": min,
        "max": max,
        "sum": sum,
        "range": range,
    }
    env: dict[str, Any] = {
        "tasks": tasks_data,
        "dependencies": deps_data,
        "branches": branches_data,
    }
    try:
        result = eval(expression, {"__builtins__": allowed_builtins}, env)  # noqa: S307
        return bool(result)
    except Exception:
        return False


class CelGate(PolicyEngine):
    """Inline CEL-style expression evaluator (pure Python, no binary).

    The ``expression`` is evaluated as a restricted Python expression with
    access to ``tasks``, ``dependencies``, and ``branches`` from the plan.
    """

    def __init__(
        self,
        name: str,
        expression: str,
        severity: str = "blocker",
        message: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        super().__init__(name, severity, message, reason_code)
        self.expression = expression

    def evaluate(self, plan: PlanVersion) -> list[Finding]:
        if _safe_eval(self.expression, plan):
            return []
        msg = self.message or f"cel policy {self.name!r} failed: {self.expression}"
        return [self._finding(plan, detail=msg)]


# ── Rego Gate ─────────────────────────────────────────────────────────────


def _find_opa() -> str | None:
    """Return the path to the ``opa`` binary, or None."""
    import shutil

    return shutil.which("opa")


def _run_opa_eval(
    rego_module: str,
    query: str,
    input_json: str,
) -> dict[str, Any]:
    """Run ``opa eval`` with a Rego module and JSON input.

    Args:
        rego_module: The Rego source code.
        query: The Rego query to execute (e.g. ``data.test.violation``).
        input_json: The input document as a JSON string.

    Returns:
        The parsed OPA result dict.

    Raises:
        RuntimeError: When ``opa`` is not found or evaluation fails.
    """
    opa = _find_opa()
    if opa is None:
        raise RuntimeError(
            "OPA binary not found. Install OPA (https://openpolicyagent.org) "
            "or use CelGate for binary-free evaluation."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        rego_path = Path(tmpdir) / "policy.rego"
        rego_path.write_text(rego_module, encoding="utf-8")
        input_path = Path(tmpdir) / "input.json"
        input_path.write_text(input_json, encoding="utf-8")

        result = subprocess.run(  # noqa: S603
            [
                opa,
                "eval",
                "--format",
                "json",
                "--data",
                str(rego_path),
                "--input",
                str(input_path),
                query,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"opa eval failed: {result.stderr.strip()}")
        parsed: dict[str, Any] = yaml.safe_load(result.stdout)
        return parsed


class RegoGate(PolicyEngine):
    """OPA/Rego policy evaluator via the ``opa`` binary.

    Requires ``opa`` to be installed and on ``PATH``. Use :class:`CelGate`
    as a binary-free alternative for simple conditions.
    """

    def __init__(
        self,
        name: str,
        module: str | Path,
        query: str,
        severity: str = "blocker",
        message: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        super().__init__(name, severity, message, reason_code)
        if isinstance(module, Path):
            self._module = module.read_text(encoding="utf-8")
        else:
            self._module = module
        self._query = query

    def evaluate(self, plan: PlanVersion) -> list[Finding]:
        input_json = plan.model_dump_json()
        try:
            result = _run_opa_eval(self._module, self._query, input_json)
        except RuntimeError as exc:
            return [
                Finding(
                    id=f"policy:{self.name}:{plan.id}:{plan.version}",
                    version=plan.version,
                    severity=Severity.WARNING,
                    reason_code="policy_evaluation_error",
                    message=f"Rego evaluation failed: {exc}",
                )
            ]

        results = result.get("result", [])
        violations: list[Any] = []
        for expr in results:
            exprs: list[Any] = expr.get("expressions", [])
            for e in exprs:
                val = e.get("value")
                if isinstance(val, dict):
                    violations.extend(val.values())
                elif isinstance(val, list):
                    violations.extend(val)
                elif val:
                    violations.append(val)

        if not violations:
            return []
        msg = self.message or f"rego policy {self.name!r} violations: {violations}"
        return [self._finding(plan, detail=msg)]


# ── Policy library (seed) ────────────────────────────────────────────────


BUILTIN_POLICIES: list[PolicyEngine] = [
    CelGate(
        name="schema_valid",
        expression="len(tasks) > 0",
        severity="blocker",
        message="Plan must have at least one task",
    ),
    CelGate(
        name="no_unsafe_ordering",
        expression=(
            "not any(d.get('kind') == 'hard'"
            " and d['from_task'] == d['to_task'] for d in dependencies)"
        ),
        severity="blocker",
        message="Self-referencing dependency detected",
    ),
    CelGate(
        name="high_risk_has_rollback",
        expression=(
            "all(t.get('rollback') is not None for t in tasks"
            " if t.get('risk_class') in ('high', 'critical'))"
        ),
        severity="blocker",
        message="High-risk tasks must have a rollback step",
    ),
    CelGate(
        name="high_risk_has_verification",
        expression=(
            "all(t.get('verification') is not None for t in tasks"
            " if t.get('risk_class') in ('high', 'critical'))"
        ),
        severity="blocker",
        message="High-risk tasks must have a verification step",
    ),
]


__all__ = [
    "BUILTIN_POLICIES",
    "CelGate",
    "PolicyEngine",
    "RegoGate",
]

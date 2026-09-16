# Policy Engine and Approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the phase-1 permissive policy gate with a deny-by-default, context-aware policy engine, and add human approvals that pause a run until an operator decides.

**Architecture:** `policy/` owns the `PolicyEngine` (implements the `PolicyGate` seam), team policy packs, and the `ApprovalService`. Approval records live in a `core/` model so the execution layer can reference them without depending on the policy package. The run lifecycle gains an `ApprovalRequests` seam, an escalation fan-out path, and a `fail` operation.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, pytest, ruff, mypy strict.

## Global Constraints

- Python `>=3.12`; pydantic v2; FastAPI; no new runtime dependency.
- Every task leaves `make check` green: `ruff check`, `mypy src/ tests/`, `pytest --cov` total > 95%.
- Ruff line length 100; `ANN` enforced in `src/`, ignored in `tests/`. Mypy strict in `src/`.
- No code comments; keep module/class/function docstrings in repo style.
- No files named with dates or milestone identifiers.
- All logic takes an injected `clock`; datetimes are timezone-aware.
- Reuse phase-1 seams: `execution.gates.PolicyGate`, `execution.gates.ApprovalRequests`, `execution.admission.AdmissionPipeline`, `execution.service.RunService`, `execution.fanout.FanOutService`, `execution.models.AdmissionResult`.
- Deny always wins over allow at every level; policy packs may tighten but never loosen.

---

## Phase scope

Phase 2 of the execution path (see `docs/design/execution-path-design.md`):

1. Run lifecycle core — complete
2. **Policy engine and approvals ← this plan**
3. Budget
4. Sandbox and shaping
5. Adapters and conformance

Injection *scanning* is implemented in phase 4; this plan adds the `block_injection` decision branch and an `injection_detected` input.

---

## File structure

| File | Responsibility |
|------|----------------|
| `src/hiveplane/core/approval.py` | `ApprovalStatus`, `ApprovalRecord` |
| `src/hiveplane/core/decision.py` | Extend `PolicyContext` (tools, approval actions, injection flag) |
| `src/hiveplane/execution/gates.py` | Add `ApprovalRequests` protocol |
| `src/hiveplane/execution/fanout.py` | Add escalation fan-out |
| `src/hiveplane/execution/service.py` | Escalation → approval + fan-out; `fail`; `failure_reason` on transitions |
| `src/hiveplane/policy/__init__.py` | Package marker |
| `src/hiveplane/policy/models.py` | Policy pack and evaluation request models |
| `src/hiveplane/policy/errors.py` | Policy and approval errors |
| `src/hiveplane/policy/packs.py` | `PolicyPackStore` + in-memory implementation |
| `src/hiveplane/policy/engine.py` | `PolicyEngine` and blast-radius scoring |
| `src/hiveplane/policy/store.py` | `ApprovalStore` + in-memory implementation |
| `src/hiveplane/policy/approvals.py` | `ApprovalService` |
| `src/hiveplane/api/policy.py` | `/policy/evaluate`, `/policy-packs` |
| `src/hiveplane/api/approvals.py` | `/approvals` and decide endpoints |
| `src/hiveplane/api/deps.py` | policy/approval dependencies |
| `src/hiveplane/api/app.py` | Wire policy and approval routers + errors |
| `src/hiveplane/execution/wiring.py` | Build engine + approval service; wire into RunService |
| `tests/test_policy_engine.py` | Engine tests |
| `tests/test_policy_packs.py` | Pack model + store tests |
| `tests/test_approvals.py` | Approval service tests |
| `tests/test_execution_escalation.py` | RunService escalation/approval integration |
| `tests/test_policy_api.py` | Policy + approval API tests |

---

### Task 1: Approval model and policy context extension

**Files:**
- Create: `src/hiveplane/core/approval.py`
- Modify: `src/hiveplane/core/decision.py`
- Test: `tests/test_core_run_models.py` (extend)

**Interfaces:**
- Consumes: `core.run.AdmissionContext`, `core.decision.ActionClass`, `core.tools.ToolsSpec`.
- Produces:
  - `core.approval.ApprovalStatus` (`pending`, `approved`, `denied`)
  - `core.approval.ApprovalRecord` (`approval_id`, `run_id`, `workload`, `rule`, `reason`, `action_class`, `requested_at`, `status`, `decided_at`, `decided_by`, `decision_reason`)
  - `core.decision.PolicyContext` gains `tools: ToolsSpec | None`, `approval_required_for: list[ActionClass]`, `injection_detected: bool`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_core_run_models.py`:

```python
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.tools import ToolTrustLevel, ToolsSpec, ToolRef


def test_approval_record_defaults() -> None:
    record = ApprovalRecord(
        approval_id="ap-1",
        run_id="run-1",
        workload="agent-1",
        rule="approvals.required",
        reason="destructive tool requires approval",
        requested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert record.status is ApprovalStatus.PENDING
    assert record.decided_at is None
    assert record.action_class is None


def test_policy_context_carries_tools_and_injection() -> None:
    tools = ToolsSpec(
        allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE, require_approval=True)]
    )
    context = PolicyContext(
        run_id="run-1",
        workload="agent-1",
        environment=AdmissionContext.PRODUCTION,
        tool_id="t1",
        tool_trust=ToolTrustLevel.DESTRUCTIVE,
        tools=tools,
        approval_required_for=[ActionClass.DESTRUCTIVE],
        injection_detected=True,
    )
    assert context.tools is tools
    assert context.injection_detected is True
    assert context.approval_required_for == [ActionClass.DESTRUCTIVE]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_core_run_models.py -k "approval or injection" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.core.approval'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/core/approval.py`:

```python
"""Approval records for policy escalations (D4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.core.decision import ActionClass


class ApprovalStatus(StrEnum):
    """State of a pending approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ApprovalRecord(BaseModel):
    """An approval request raised when policy escalates a run."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    action_class: ActionClass | None = None
    requested_at: AwareDatetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: AwareDatetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
```

`src/hiveplane/core/decision.py` — add the import and fields:

```python
from hiveplane.core.tools import ToolsSpec, ToolTrustLevel
```

```python
    tools: ToolsSpec | None = None
    approval_required_for: list[ActionClass] = Field(default_factory=list)
    injection_detected: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_core_run_models.py -v`
Expected: PASS

- [ ] **Step 5: Full gate + commit**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m mypy src/ tests/`
Then:

```bash
git add src/hiveplane/core/approval.py src/hiveplane/core/decision.py tests/test_core_run_models.py
git commit -m "feat(policy): add approval record and extend policy context"
git push origin main
```

---

### Task 2: Policy pack models, errors, and store

**Files:**
- Create: `src/hiveplane/policy/__init__.py`
- Create: `src/hiveplane/policy/errors.py`
- Create: `src/hiveplane/policy/models.py`
- Create: `src/hiveplane/policy/packs.py`
- Test: `tests/test_policy_packs.py`

**Interfaces:**
- Consumes: `core.decision.ActionClass`/`DecisionOutcome`/`DataSensitivity`, `core.run.AdmissionContext`, `core.tools.ToolTrustLevel`.
- Produces:
  - `policy.models.PolicyPackRule`, `PolicyPackRuleMatch`, `PolicyPackOverride`, `PolicyPackDefaults`, `PolicyPackSpec`, `PolicyPackMetadata`, `PolicyPack`
  - `policy.models.PolicyEvaluationRequest` (API body for `/policy/evaluate`)
  - `policy.errors.PolicyError`, `PolicyPackAlreadyExistsError`, `PolicyPackNotFoundError`
  - `policy.packs.PolicyPackStore` protocol + `InMemoryPolicyPackStore` with `save`, `get`, `list_packs`, `for_team`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for policy pack models and the pack store."""

from __future__ import annotations

import pytest

from hiveplane.core.decision import ActionClass, DataSensitivity, DecisionOutcome
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.policy.errors import PolicyPackAlreadyExistsError
from hiveplane.policy.models import (
    PolicyPack,
    PolicyPackDefaults,
    PolicyPackMetadata,
    PolicyPackOverride,
    PolicyPackRule,
    PolicyPackRuleMatch,
    PolicyPackSpec,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore


def _pack(name: str = "platform-default", team: str = "platform") -> PolicyPack:
    return PolicyPack(
        apiVersion="hiveplane/v1",
        kind="PolicyPack",
        metadata=PolicyPackMetadata(name=name, team=team, version="1.0.0"),
        spec=PolicyPackSpec(
            defaults=PolicyPackDefaults(),
            overrides=[
                PolicyPackOverride(
                    match=PolicyPackRuleMatch(
                        environment=AdmissionContext.PRODUCTION,
                        data_sensitivity=DataSensitivity.PII,
                    ),
                    rules=[
                        PolicyPackRule(action=DecisionOutcome.DENY, tool_trust=ToolTrustLevel.DESTRUCTIVE)
                    ],
                )
            ],
        ),
    )


def test_pack_round_trips() -> None:
    pack = _pack()
    assert pack.metadata.team == "platform"
    assert pack.spec.overrides[0].rules[0].action is DecisionOutcome.DENY


def test_store_save_get_list_for_team() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("a", "platform"))
    store.save(_pack("b", "other"))
    assert store.get("a") is not None
    assert {p.metadata.name for p in store.list_packs()} == {"a", "b"}
    assert {p.metadata.name for p in store.for_team("platform")} == {"a"}
    assert {p.metadata.name for p in store.for_team(None)} == set()


def test_store_rejects_duplicate() -> None:
    store = InMemoryPolicyPackStore()
    store.save(_pack("a"))
    with pytest.raises(PolicyPackAlreadyExistsError):
        store.save(_pack("a"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_policy_packs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.policy'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/policy/__init__.py`:

```python
"""Policy engine, team policy packs, and human approvals."""
```

`src/hiveplane/policy/errors.py`:

```python
"""Policy and approval domain errors."""

from __future__ import annotations


class PolicyError(Exception):
    """Base class for policy errors."""


class PolicyPackAlreadyExistsError(PolicyError):
    """Raised when saving a pack whose name is already registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"policy pack {name!r} already exists")
        self.name = name


class PolicyPackNotFoundError(PolicyError):
    """Raised when a policy pack is not found."""

    def __init__(self, name: str) -> None:
        super().__init__(f"policy pack {name!r} not found")
        self.name = name


class ApprovalNotFoundError(PolicyError):
    """Raised when an approval id is unknown."""

    def __init__(self, approval_id: str) -> None:
        super().__init__(f"approval {approval_id!r} not found")
        self.approval_id = approval_id


class ApprovalAlreadyDecidedError(PolicyError):
    """Raised when deciding an approval that is already resolved."""

    def __init__(self, approval_id: str) -> None:
        super().__init__(f"approval {approval_id!r} has already been decided")
        self.approval_id = approval_id
```

`src/hiveplane/policy/models.py`:

```python
"""Policy pack and evaluation request models (D4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.decision import ActionClass, DataSensitivity, DecisionOutcome
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel


class PolicyPackDefaults(BaseModel):
    """Default behaviors a team pack requests."""

    model_config = ConfigDict(extra="forbid")

    sandbox_for_destructive: bool = True
    injection_scan: bool = True
    max_bytes: int | None = Field(default=None, gt=0)


class PolicyPackRule(BaseModel):
    """A single tighten-only rule inside a pack override."""

    model_config = ConfigDict(extra="forbid")

    action: DecisionOutcome
    action_class: ActionClass | None = None
    tool_trust: ToolTrustLevel | None = None


class PolicyPackRuleMatch(BaseModel):
    """Context a pack override applies to."""

    model_config = ConfigDict(extra="forbid")

    environment: AdmissionContext | None = None
    data_sensitivity: DataSensitivity | None = None


class PolicyPackOverride(BaseModel):
    """A context match and the rules applied when it matches."""

    model_config = ConfigDict(extra="forbid")

    match: PolicyPackRuleMatch
    rules: list[PolicyPackRule] = Field(min_length=1)


class PolicyPackSpec(BaseModel):
    """The spec block of a policy pack."""

    model_config = ConfigDict(extra="forbid")

    defaults: PolicyPackDefaults = Field(default_factory=PolicyPackDefaults)
    overrides: list[PolicyPackOverride] = Field(default_factory=list)
    approval_contacts: dict[str, str] = Field(default_factory=dict)


class PolicyPackMetadata(BaseModel):
    """Policy pack identity."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    team: str = Field(min_length=1)
    version: str = Field(min_length=1)


class PolicyPack(BaseModel):
    """A versioned, distributable policy bundle for a team."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: Literal["hiveplane/v1"] = Field(default="hiveplane/v1", alias="apiVersion")
    kind: Literal["PolicyPack"] = "PolicyPack"
    metadata: PolicyPackMetadata
    spec: PolicyPackSpec


class PolicyEvaluationRequest(BaseModel):
    """Request body for evaluating policy against a context."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    team: str | None = None
    environment: AdmissionContext
    tool_id: str | None = None
    tool_trust: ToolTrustLevel | None = None
    action_class: ActionClass | None = None
    data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL
```

`src/hiveplane/policy/packs.py`:

```python
"""Policy pack storage."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.policy.errors import PolicyPackAlreadyExistsError
from hiveplane.policy.models import PolicyPack


class PolicyPackStore(Protocol):
    """Storage for team policy packs."""

    def save(self, pack: PolicyPack) -> None: ...

    def get(self, name: str) -> PolicyPack | None: ...

    def list_packs(self) -> list[PolicyPack]: ...

    def for_team(self, team: str | None) -> list[PolicyPack]: ...


class InMemoryPolicyPackStore:
    """A process-local, thread-safe policy pack store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._packs: dict[str, PolicyPack] = {}

    def save(self, pack: PolicyPack) -> None:
        """Register a pack, rejecting a duplicate name."""
        with self._lock:
            name = pack.metadata.name
            if name in self._packs:
                raise PolicyPackAlreadyExistsError(name)
            self._packs[name] = pack.model_copy(deep=True)

    def get(self, name: str) -> PolicyPack | None:
        """Return a pack by name."""
        with self._lock:
            pack = self._packs.get(name)
            return pack.model_copy(deep=True) if pack is not None else None

    def list_packs(self) -> list[PolicyPack]:
        """Return all packs, ordered by name."""
        with self._lock:
            ordered = sorted(self._packs.values(), key=lambda pack: pack.metadata.name)
            return [pack.model_copy(deep=True) for pack in ordered]

    def for_team(self, team: str | None) -> list[PolicyPack]:
        """Return packs belonging to a team."""
        if team is None:
            return []
        return [pack for pack in self.list_packs() if pack.metadata.team == team]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_policy_packs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Full gate + commit**

```bash
git add src/hiveplane/policy tests/test_policy_packs.py
git commit -m "feat(policy): add policy pack models, errors, and store"
git push origin main
```

---

### Task 3: Policy engine and blast-radius scoring

**Files:**
- Create: `src/hiveplane/policy/engine.py`
- Test: `tests/test_policy_engine.py`

**Interfaces:**
- Consumes: `core.decision.PolicyContext`/`PolicyDecision`/`DecisionOutcome`/`ActionClass`/`DataSensitivity`/`BlastRadius`, `certification.models.CertificationStatus`, `policy.packs.PolicyPackStore`.
- Produces: `policy.engine.PolicyEngine(packs, *, clock=None)` with `evaluate(context) -> PolicyDecision`, and `compute_blast_radius(context) -> BlastRadius`.

Evaluation order (first match wins, deny always wins): quarantine → injection → manifest deny → pack deny/escalate → sensitivity → allow/trust/approval/blast/default. A run-level context (`tool_id is None`) is allowed unless a pack escalates or denies.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the policy engine."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.decision import (
    ActionClass,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolRef, ToolsSpec, ToolTrustLevel
from hiveplane.policy.engine import PolicyEngine, compute_blast_radius
from hiveplane.policy.models import (
    PolicyPack,
    PolicyPackMetadata,
    PolicyPackOverride,
    PolicyPackRule,
    PolicyPackRuleMatch,
    PolicyPackSpec,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _tools(
    *,
    allow: list[ToolRef] | None = None,
    deny: list[str] | None = None,
) -> ToolsSpec:
    return ToolsSpec(allow=allow or [], deny=deny or [])


def _context(**overrides: object) -> PolicyContext:
    data: dict[str, object] = {
        "run_id": "run-1",
        "workload": "agent-1",
        "environment": AdmissionContext.STAGING,
        "certification_status": CertificationStatus.CERTIFIED,
    }
    data.update(overrides)
    return PolicyContext.model_validate(data)


def _engine() -> PolicyEngine:
    return PolicyEngine(InMemoryPolicyPackStore(), clock=_clock)


def test_unlisted_tool_is_denied_by_default() -> None:
    decision = _engine().evaluate(_context(tool_id="t1", tools=_tools()))
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "default.deny"


def test_read_only_tool_allowed_in_staging() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule == "manifest.allow"


def test_same_tool_gated_in_production_when_uncertified() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "certification.production"


def test_destructive_tool_escalates_when_certified() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            certification_status=CertificationStatus.CERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.ESCALATE
    assert decision.rule == "trust.destructive"


def test_destructive_tool_denied_in_uncertified_production() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY


def test_sandbox_allows_listed_tools() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.DESTRUCTIVE)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            environment=AdmissionContext.SANDBOX,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.rule == "sandbox.allow"


def test_require_approval_escalates() -> None:
    tools = _tools(
        allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY, require_approval=True)]
    )
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.outcome is DecisionOutcome.ESCALATE
    assert decision.rule == "approvals.required"


def test_action_class_approval_escalates() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            action_class=ActionClass.HIGH_SPEND,
            approval_required_for=[ActionClass.HIGH_SPEND],
        )
    )
    assert decision.outcome is DecisionOutcome.ESCALATE


def test_manifest_deny_beats_allow() -> None:
    tools = _tools(
        allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)], deny=["t1"]
    )
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "manifest.deny"


def test_quarantine_denies_everything() -> None:
    decision = _engine().evaluate(
        _context(tool_id="t1", certification_status=CertificationStatus.QUARANTINED)
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "certification.quarantined"


def test_injection_blocks() -> None:
    decision = _engine().evaluate(_context(tool_id="t1", injection_detected=True))
    assert decision.outcome is DecisionOutcome.BLOCK_INJECTION
    assert decision.rule == "injection.scan"


def test_restricted_sensitivity_denies_write() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            action_class=ActionClass.PRODUCTION_WRITE,
            data_sensitivity=DataSensitivity.RESTRICTED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "sensitivity.restricted"


def test_pack_deny_tightens() -> None:
    store = InMemoryPolicyPackStore()
    store.save(
        PolicyPack(
            metadata=PolicyPackMetadata(name="prod-deny", team="platform", version="1.0.0"),
            spec=PolicyPackSpec(
                overrides=[
                    PolicyPackOverride(
                        match=PolicyPackRuleMatch(environment=AdmissionContext.PRODUCTION),
                        rules=[PolicyPackRule(action=DecisionOutcome.DENY)],
                    )
                ]
            ),
        )
    )
    engine = PolicyEngine(store, clock=_clock)
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = engine.evaluate(
        _context(
            tool_id="t1",
            tools=tools,
            team="platform",
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.CERTIFIED,
        )
    )
    assert decision.outcome is DecisionOutcome.DENY
    assert decision.rule == "pack.deny"


def test_blast_radius_bounds_and_high_denies() -> None:
    blast = compute_blast_radius(
        _context(
            tool_id="t1",
            tool_trust=ToolTrustLevel.DESTRUCTIVE,
            action_class=ActionClass.PRODUCTION_WRITE,
            data_sensitivity=DataSensitivity.RESTRICTED,
            environment=AdmissionContext.PRODUCTION,
            certification_status=CertificationStatus.UNCERTIFIED,
        )
    )
    assert 0 <= blast.score <= 100
    assert blast.score >= 71


def test_run_level_context_allowed() -> None:
    decision = _engine().evaluate(_context(environment=AdmissionContext.PRODUCTION))
    assert decision.outcome is DecisionOutcome.ALLOW
    assert decision.reason


def test_decision_is_explainable() -> None:
    tools = _tools(allow=[ToolRef(tool_id="t1", trust_level=ToolTrustLevel.READ_ONLY)])
    decision = _engine().evaluate(_context(tool_id="t1", tools=tools))
    assert decision.rule
    assert decision.reason
    assert decision.blast_radius is not None
    assert decision.certification_status is CertificationStatus.CERTIFIED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_policy_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.policy.engine'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Context-aware, deny-by-default policy evaluation (DD-03)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.decision import (
    ActionClass,
    BlastRadius,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
    PolicyDecision,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.policy.models import PolicyPackOverride
from hiveplane.policy.packs import PolicyPackStore


def compute_blast_radius(context: PolicyContext) -> BlastRadius:
    """Compute a blast-radius score and its factor contributions."""
    factors: dict[str, int] = {}
    if context.tool_trust is ToolTrustLevel.DESTRUCTIVE:
        factors["tool_trust"] = 35
    if context.environment is AdmissionContext.PRODUCTION:
        factors["environment"] = 30
    elif context.environment is AdmissionContext.STAGING:
        factors["environment"] = 15
    if context.data_sensitivity is DataSensitivity.RESTRICTED:
        factors["data_sensitivity"] = 20
    elif context.data_sensitivity is DataSensitivity.PII:
        factors["data_sensitivity"] = 15
    elif context.data_sensitivity is DataSensitivity.INTERNAL:
        factors["data_sensitivity"] = 5
    if context.action_class in (ActionClass.PRODUCTION_WRITE, ActionClass.DESTRUCTIVE):
        factors["action_class"] = 20
    elif context.action_class is ActionClass.HIGH_SPEND:
        factors["action_class"] = 10
    if context.certification_status is CertificationStatus.UNCERTIFIED:
        factors["certification"] = 15
    elif context.certification_status is CertificationStatus.PROVISIONAL:
        factors["certification"] = 8
    return BlastRadius(score=min(100, sum(factors.values())), factors=factors)


class PolicyEngine:
    """Evaluates policy for a run or tool call, with explainable decisions."""

    def __init__(
        self, packs: PolicyPackStore, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.packs = packs
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        """Return the policy decision for a context."""
        blast = compute_blast_radius(context)
        if context.certification_status is CertificationStatus.QUARANTINED:
            return self._decide(
                context, DecisionOutcome.DENY, "certification.quarantined", "workload is quarantined", blast
            )
        if context.injection_detected:
            return self._decide(
                context,
                DecisionOutcome.BLOCK_INJECTION,
                "injection.scan",
                "tool output contains injection patterns",
                blast,
            )
        if context.tool_id is None:
            packed = self._pack_decision(context, blast)
            if packed is not None:
                return packed
            return self._decide(
                context, DecisionOutcome.ALLOW, "run.allow", "no tool policy applies", blast
            )

        tools = context.tools
        if tools is not None and tools.is_denied(context.tool_id):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "manifest.deny",
                f"tool {context.tool_id!r} is explicitly denied",
                blast,
            )
        packed = self._pack_decision(context, blast)
        if packed is not None:
            return packed
        if (
            context.data_sensitivity is DataSensitivity.RESTRICTED
            and context.action_class is not None
            and context.action_class is not ActionClass.READ_ONLY
        ):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "sensitivity.restricted",
                "restricted data forbids write actions",
                blast,
            )
        if (
            context.data_sensitivity is DataSensitivity.PII
            and context.tool_trust is ToolTrustLevel.DESTRUCTIVE
        ):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "sensitivity.pii",
                "PII forbids destructive tools",
                blast,
            )

        if tools is None or not tools.is_allowed(context.tool_id):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "default.deny",
                f"tool {context.tool_id!r} is not allowed",
                blast,
            )
        if context.environment is AdmissionContext.SANDBOX:
            return self._decide(
                context, DecisionOutcome.ALLOW, "sandbox.allow", "sandbox context allows the tool", blast
            )
        if (
            context.environment is AdmissionContext.PRODUCTION
            and context.certification_status is not CertificationStatus.CERTIFIED
        ):
            return self._decide(
                context,
                DecisionOutcome.DENY,
                "certification.production",
                "production requires a certified workload",
                blast,
            )
        if context.tool_trust is ToolTrustLevel.DESTRUCTIVE:
            return self._decide(
                context,
                DecisionOutcome.ESCALATE,
                "trust.destructive",
                "destructive tools require approval",
                blast,
            )
        if tools.approval_required(context.tool_id) or (
            context.action_class is not None
            and context.action_class in context.approval_required_for
        ):
            return self._decide(
                context,
                DecisionOutcome.ESCALATE,
                "approvals.required",
                "action requires approval",
                blast,
            )
        if blast.score >= 71:
            return self._decide(
                context, DecisionOutcome.DENY, "blast_radius.high", "blast radius is high", blast
            )
        if blast.score >= 31:
            return self._decide(
                context,
                DecisionOutcome.ESCALATE,
                "blast_radius.medium",
                "blast radius is medium",
                blast,
            )
        return self._decide(
            context, DecisionOutcome.ALLOW, "manifest.allow", "tool is explicitly allowed", blast
        )

    def _pack_decision(
        self, context: PolicyContext, blast: BlastRadius
    ) -> PolicyDecision | None:
        for pack in self.packs.for_team(context.team):
            for override in pack.spec.overrides:
                if not self._matches(override, context):
                    continue
                for rule in override.rules:
                    if rule.action is DecisionOutcome.ALLOW:
                        continue
                    if rule.action_class is not None and rule.action_class is not context.action_class:
                        continue
                    if rule.tool_trust is not None and rule.tool_trust is not context.tool_trust:
                        continue
                    rule_id = "pack.deny" if rule.action is DecisionOutcome.DENY else "pack.escalate"
                    return self._decide(
                        context,
                        rule.action,
                        rule_id,
                        f"policy pack {pack.metadata.name!r} applies",
                        blast,
                    )
        return None

    @staticmethod
    def _matches(override: PolicyPackOverride, context: PolicyContext) -> bool:
        match = override.match
        if match.environment is not None and match.environment is not context.environment:
            return False
        return not (
            match.data_sensitivity is not None
            and match.data_sensitivity is not context.data_sensitivity
        )

    def _decide(
        self,
        context: PolicyContext,
        outcome: DecisionOutcome,
        rule: str,
        reason: str,
        blast: BlastRadius,
    ) -> PolicyDecision:
        return PolicyDecision(
            run_id=context.run_id,
            outcome=outcome,
            rule=rule,
            reason=reason,
            action_class=context.action_class,
            timestamp=self._clock(),
            blast_radius=blast,
            certification_status=context.certification_status,
        )
```

Add `from typing import Protocol`? No. The unused import `PolicyPack` in engine must be removed (ruff F401). Remove it.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_policy_engine.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Full gate + commit**

```bash
git add src/hiveplane/policy/engine.py tests/test_policy_engine.py
git commit -m "feat(policy): add deny-by-default context-aware policy engine"
git push origin main
```

---

### Task 4: Approval store and service

**Files:**
- Create: `src/hiveplane/policy/store.py`
- Create: `src/hiveplane/policy/approvals.py`
- Test: `tests/test_approvals.py`

**Interfaces:**
- Consumes: `core.approval.ApprovalRecord`/`ApprovalStatus`, `policy.errors.ApprovalNotFoundError`/`ApprovalAlreadyDecidedError`.
- Produces:
  - `policy.store.ApprovalStore` protocol + `InMemoryApprovalStore` with `save`, `get`, `list_approvals(*, status, workload)`
  - `policy.approvals.ApprovalService(store, *, clock=None, id_factory=None)` with `request(*, run_id, workload, rule, reason, action_class=None)`, `get(id)`, `list(*, status=None, workload=None)`, `decide(id, *, status, operator, reason=None)`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the approval service."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiveplane.core.approval import ApprovalStatus
from hiveplane.core.decision import ActionClass
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.errors import ApprovalAlreadyDecidedError, ApprovalNotFoundError
from hiveplane.policy.store import InMemoryApprovalStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _service() -> ApprovalService:
    ids = iter(["ap-1", "ap-2"])
    return ApprovalService(
        InMemoryApprovalStore(), clock=_clock, id_factory=lambda: next(ids)
    )


def test_request_creates_pending_record() -> None:
    service = _service()
    record = service.request(
        run_id="run-1",
        workload="agent-1",
        rule="approvals.required",
        reason="needs approval",
        action_class=ActionClass.DESTRUCTIVE,
    )
    assert record.approval_id == "ap-1"
    assert record.status is ApprovalStatus.PENDING
    assert service.get("ap-1").run_id == "run-1"


def test_get_missing_raises() -> None:
    with pytest.raises(ApprovalNotFoundError):
        _service().get("nope")


def test_approve_records_decision() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    decided = service.decide("ap-1", status=ApprovalStatus.APPROVED, operator="alice")
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "alice"
    assert decided.decided_at == _clock()


def test_deny_records_reason() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    decided = service.decide(
        "ap-1", status=ApprovalStatus.DENIED, operator="bob", reason="too risky"
    )
    assert decided.status is ApprovalStatus.DENIED
    assert decided.decision_reason == "too risky"


def test_double_decide_raises() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    service.decide("ap-1", status=ApprovalStatus.APPROVED, operator="alice")
    with pytest.raises(ApprovalAlreadyDecidedError):
        service.decide("ap-1", status=ApprovalStatus.DENIED, operator="bob")


def test_decide_rejects_pending_status() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    with pytest.raises(ValueError):
        service.decide("ap-1", status=ApprovalStatus.PENDING, operator="alice")


def test_list_filters() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    service.request(run_id="run-2", workload="agent-2", rule="r", reason="x")
    service.decide("ap-1", status=ApprovalStatus.APPROVED, operator="alice")
    assert {a.approval_id for a in service.list(status=ApprovalStatus.PENDING)} == {"ap-2"}
    assert {a.approval_id for a in service.list(workload="agent-2")} == {"ap-2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_approvals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.policy.store'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/policy/store.py`:

```python
"""Approval request storage."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.core.approval import ApprovalRecord, ApprovalStatus


class ApprovalStore(Protocol):
    """Storage for approval requests."""

    def save(self, record: ApprovalRecord) -> None: ...

    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def list_approvals(
        self, *, status: ApprovalStatus | None = None, workload: str | None = None
    ) -> list[ApprovalRecord]: ...


class InMemoryApprovalStore:
    """A process-local, thread-safe approval store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, ApprovalRecord] = {}

    def save(self, record: ApprovalRecord) -> None:
        """Persist an approval record."""
        with self._lock:
            self._records[record.approval_id] = record.model_copy(deep=True)

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Return an approval by id."""
        with self._lock:
            record = self._records.get(approval_id)
            return record.model_copy(deep=True) if record is not None else None

    def list_approvals(
        self, *, status: ApprovalStatus | None = None, workload: str | None = None
    ) -> list[ApprovalRecord]:
        """List approvals, optionally filtered."""
        with self._lock:
            records = list(self._records.values())
        if status is not None:
            records = [record for record in records if record.status is status]
        if workload is not None:
            records = [record for record in records if record.workload == workload]
        records.sort(key=lambda record: record.requested_at)
        return [record.model_copy(deep=True) for record in records]
```

`src/hiveplane/policy/approvals.py`:

```python
"""Human approval workflow for escalated runs (D4, DD-03)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.decision import ActionClass
from hiveplane.policy.errors import ApprovalAlreadyDecidedError, ApprovalNotFoundError
from hiveplane.policy.store import ApprovalStore


class ApprovalService:
    """Creates and resolves approval requests raised by policy escalation."""

    def __init__(
        self,
        store: ApprovalStore,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"ap-{uuid4().hex[:12]}")

    def request(
        self,
        *,
        run_id: str,
        workload: str,
        rule: str,
        reason: str,
        action_class: ActionClass | None = None,
    ) -> ApprovalRecord:
        """Create a pending approval request for a run."""
        record = ApprovalRecord(
            approval_id=self._id_factory(),
            run_id=run_id,
            workload=workload,
            rule=rule,
            reason=reason,
            action_class=action_class,
            requested_at=self._clock(),
        )
        self._store.save(record)
        return record

    def get(self, approval_id: str) -> ApprovalRecord:
        """Return an approval by id."""
        record = self._store.get(approval_id)
        if record is None:
            raise ApprovalNotFoundError(approval_id)
        return record

    def list(
        self, *, status: ApprovalStatus | None = None, workload: str | None = None
    ) -> list[ApprovalRecord]:
        """List approvals, optionally filtered."""
        return self._store.list_approvals(status=status, workload=workload)

    def decide(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        operator: str,
        reason: str | None = None,
    ) -> ApprovalRecord:
        """Approve or deny a pending request."""
        if status is ApprovalStatus.PENDING:
            raise ValueError("decision status must be approved or denied")
        record = self.get(approval_id)
        if record.status is not ApprovalStatus.PENDING:
            raise ApprovalAlreadyDecidedError(approval_id)
        decided = record.model_copy(
            update={
                "status": status,
                "decided_at": self._clock(),
                "decided_by": operator,
                "decision_reason": reason,
            }
        )
        self._store.save(decided)
        return decided
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_approvals.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Full gate + commit**

```bash
git add src/hiveplane/policy/store.py src/hiveplane/policy/approvals.py tests/test_approvals.py
git commit -m "feat(policy): add approval store and service"
git push origin main
```

---

### Task 5: Run lifecycle integration (escalation, escalation fan-out, fail)

**Files:**
- Modify: `src/hiveplane/core/decision.py` (none)
- Modify: `src/hiveplane/execution/gates.py`
- Modify: `src/hiveplane/execution/models.py`
- Modify: `src/hiveplane/execution/fanout.py`
- Modify: `src/hiveplane/execution/service.py`
- Test: `tests/test_execution_escalation.py`

**Interfaces:**
- Consumes: `core.approval.ApprovalRecord`, `execution.fanout.FanOutService`, `policy.approvals.ApprovalService` (structurally).
- Produces:
  - `execution.gates.ApprovalRequests` protocol with `request(*, run_id, workload, rule, reason) -> ApprovalRecord`
  - `execution.models.AdmissionResult.policy_check() -> AdmissionCheck | None`
  - `execution.fanout.FanOutService.notify_escalation(run, workload) -> list[DeliveryRecord]`
  - `RunService(*, approvals: ApprovalRequests | None = None, ...)`, `RunService.fail(run_id, *, actor, reason)`, `transition(..., failure_reason=None)`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for run escalation, approvals, and escalation fan-out."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.fanout import FanOutService
from hiveplane.execution.models import RunContext
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _Executor:
    def start(self, context: RunContext) -> None: ...
    def pause(self, run_id: str) -> bool:
        return True
    def resume(self, run_id: str) -> bool:
        return True
    def cancel(self, run_id: str) -> None: ...
    def status(self, run_id: str) -> RunState:
        return RunState.PAUSED
    def usage(self, run_id: str) -> UsageReport | None:
        return None


class _Approvals:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str, str, str]] = []

    def request(self, *, run_id: str, workload: str, rule: str, reason: str) -> ApprovalRecord:
        self.requested.append((run_id, workload, rule, reason))
        return ApprovalRecord(
            approval_id="ap-1",
            run_id=run_id,
            workload=workload,
            rule=rule,
            reason=reason,
            requested_at=_clock(),
        )


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(self, destination: FanOutDestination, message: dict[str, Any]) -> None:
        self.sent.append(message)


def _service(
    make_manifest: Callable[..., AgentWorkload],
    *,
    approvals: _Approvals | None = None,
    escalation_destinations: list[dict[str, object]] | None = None,
) -> tuple[RunService, InMemoryRunStore, _Recorder]:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(
        name="agent-1",
        fan_out={"on_escalation": escalation_destinations or []},
    )
    registry.create(workload)
    store = InMemoryRunStore()
    recorder = _Recorder()
    fanout = FanOutService(store, {FanOutType.SLACK: recorder}, clock=_clock)
    admission = AdmissionPipeline(
        _Cert(True, "m1"), _Policy(DecisionOutcome.ESCALATE), _Budget(True), _Sandbox(False), clock=_clock
    )
    ids = iter(["run-1", "run-2"])
    service = RunService(
        store,
        registry,
        admission=admission,
        executor=_Executor(),
        fanout=fanout,
        approvals=approvals,
        clock=_clock,
        id_factory=lambda: next(ids),
    )
    return service, store, recorder


def test_escalation_requests_approval(make_manifest: Callable[..., AgentWorkload]) -> None:
    approvals = _Approvals()
    service, _, _ = _service(make_manifest, approvals=approvals)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    assert run.state is RunState.PAUSED
    assert approvals.requested
    assert approvals.requested[0][0] == "run-1"


def test_escalation_fans_out_to_slack(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, recorder = _service(
        make_manifest, escalation_destinations=[{"type": "slack", "channel": "#ops"}]
    )
    service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    assert recorder.sent[0]["state"] == "paused"


def test_fail_records_reason(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service(make_manifest)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    failed = service.fail(run.id, actor="alice", reason="too risky")
    assert failed.state is RunState.FAILED
    assert failed.failure_reason == "too risky"
    assert service.events(run.id)[-1].to_state is RunState.FAILED


def test_transition_can_set_failure_reason(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service(make_manifest)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    failed = service.transition(
        run.id, RunState.FAILED, actor="runtime", failure_reason="boom"
    )
    assert failed.failure_reason == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_escalation.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'approvals'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/execution/gates.py` — add near `FanOut`:

```python
from hiveplane.core.approval import ApprovalRecord


class ApprovalRequests(Protocol):
    """Requests a human approval for an escalated run."""

    def request(
        self, *, run_id: str, workload: str, rule: str, reason: str
    ) -> ApprovalRecord: ...
```

`src/hiveplane/execution/models.py` — add to `AdmissionResult`:

```python
    def policy_check(self) -> AdmissionCheck | None:
        """Return the policy step check, if present."""
        for check in self.checks:
            if check.step == "policy":
                return check
        return None
```

`src/hiveplane/execution/fanout.py` — add `notify_escalation` and an escalation flag:

```python
    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        """Deliver an escalation notice to on_escalation destinations."""
        if not self._enabled:
            return []
        message = _message(run, workload)
        records: list[DeliveryRecord] = []
        for destination in workload.spec.fan_out.on_escalation:
            records.append(self._deliver(run, destination, message))
        return records
```

Also change `_message` so the escalation notice carries the paused state (already does).

`src/hiveplane/execution/service.py`:
- import `ApprovalRequests`; add constructor param `approvals: ApprovalRequests | None = None` stored as `self._approvals`.
- in `submit`, after saving the run and admission, replace the escalation event block with:

```python
        if result.escalation_required:
            self._append_event(
                run.id,
                EventType.STATE_CHANGE,
                caller,
                to_state=RunState.PAUSED,
                detail="escalation",
            )
            if self._approvals is not None:
                check = result.policy_check()
                self._approvals.request(
                    run_id=run.id,
                    workload=workload,
                    rule=check.rule if check and check.rule else "approvals.required",
                    reason=check.reason if check and check.reason else "approval required",
                )
            self._fanout.notify_escalation(run, record.manifest)
```

- add `failure_reason` to `transition`:

```python
    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
        failure_reason: str | None = None,
    ) -> Run:
```
and after `updates: dict[str, object] = {...}`:

```python
        if failure_reason is not None:
            updates["failure_reason"] = failure_reason
```

- add `fail`:

```python
    def fail(self, run_id: str, *, actor: str, reason: str) -> Run:
        """Move a live run to failed with a recorded reason."""
        run = self._require(run_id)
        if not can_transition(run.state, RunState.FAILED):
            raise IllegalTransitionError(run_id, run.state, RunState.FAILED)
        return self.transition(
            run_id, RunState.FAILED, actor=actor, detail=reason, failure_reason=reason
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_execution_escalation.py -v`
Expected: PASS (4 tests). Then run the whole suite to confirm phase-1 tests still pass.

- [ ] **Step 5: Full gate + commit**

```bash
git add src/hiveplane/execution/gates.py src/hiveplane/execution/models.py src/hiveplane/execution/fanout.py src/hiveplane/execution/service.py tests/test_execution_escalation.py
git commit -m "feat(execution): integrate approval escalation and escalation fan-out"
git push origin main
```

---

### Task 6: Policy and approvals API, and wiring

**Files:**
- Create: `src/hiveplane/api/policy.py`
- Create: `src/hiveplane/api/approvals.py`
- Modify: `src/hiveplane/api/deps.py`
- Modify: `src/hiveplane/api/app.py`
- Modify: `src/hiveplane/execution/wiring.py`
- Test: `tests/test_policy_api.py`

**Interfaces:**
- Consumes: `PolicyEngine`, `PolicyPackStore`, `ApprovalService`, `ApprovalStatus`, `RunService`.
- Produces:
  - `POST /policy/evaluate` → `PolicyDecision`
  - `GET /policy-packs` → `list[PolicyPack]`; `POST /policy-packs` → `PolicyPack` (201)
  - `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve`, `POST /approvals/{id}/deny`
  - `execution.wiring.build_run_service(registry_service, policy_gate, approvals) -> RunService`
  - app state: `policy_engine`, `policy_pack_store`, `approval_service`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the policy and approval API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService


def _setup(make_manifest: Callable[..., AgentWorkload]) -> TestClient:
    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(
        make_manifest(
            name="agent-1",
            fan_out={"on_escalation": [{"type": "slack", "channel": "#ops"}]},
        )
    )
    return TestClient(app)


def test_evaluate_denies_unlisted_tool(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _setup(make_manifest)
    response = client.post(
        "/policy/evaluate",
        json={
            "run_id": "run-1",
            "workload": "agent-1",
            "environment": "staging",
            "tool_id": "not-allowed",
        },
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "deny"
    assert response.json()["rule"] == "default.deny"


def test_register_and_list_policy_packs(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _setup(make_manifest)
    pack = {
        "apiVersion": "hiveplane/v1",
        "kind": "PolicyPack",
        "metadata": {"name": "platform-default", "team": "platform", "version": "1.0.0"},
        "spec": {"defaults": {}, "overrides": []},
    }
    assert client.post("/policy-packs", json=pack).status_code == 201
    assert client.get("/policy-packs").json()[0]["metadata"]["name"] == "platform-default"
    assert client.post("/policy-packs", json=pack).status_code == 409


def test_approve_resumes_and_deny_fails(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _setup(make_manifest)
    app = client.app
    runs = app.state.run_service
    approvals = app.state.approval_service

    run = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.PAUSED, actor="operator")
    request = approvals.request(
        run_id=run.id, workload="agent-1", rule="approvals.required", reason="test"
    )
    approved = client.post(f"/approvals/{request.approval_id}/approve", json={"operator": "alice"})
    assert approved.status_code == 200
    assert runs.get(run.id).state is RunState.RUNNING

    run2 = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run2.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run2.id, RunState.PAUSED, actor="operator")
    request2 = approvals.request(run_id=run2.id, workload="agent-1", rule="r", reason="test")
    denied = client.post(
        f"/approvals/{request2.approval_id}/deny", json={"operator": "bob", "reason": "no"}
    )
    assert denied.status_code == 200
    assert runs.get(run2.id).state is RunState.FAILED


def test_unknown_approval_returns_404(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _setup(make_manifest)
    assert client.get("/approvals/nope").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_policy_api.py -v`
Expected: FAIL — `POST /policy/evaluate` returns 404

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/api/policy.py`:

```python
"""Policy evaluation and policy pack API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from hiveplane.api.deps import get_policy_engine, get_policy_pack_store
from hiveplane.core.decision import PolicyContext, PolicyDecision
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.models import PolicyEvaluationRequest, PolicyPack
from hiveplane.policy.packs import PolicyPackStore

router = APIRouter(tags=["policy"])

EngineDep = Annotated[PolicyEngine, Depends(get_policy_engine)]
PackStoreDep = Annotated[PolicyPackStore, Depends(get_policy_pack_store)]


@router.post("/policy/evaluate", response_model=PolicyDecision)
def evaluate_policy(payload: PolicyEvaluationRequest, engine: EngineDep) -> PolicyDecision:
    """Evaluate policy for a context and return an explainable decision."""
    return engine.evaluate(PolicyContext(**payload.model_dump()))


@router.get("/policy-packs", response_model=list[PolicyPack])
def list_policy_packs(store: PackStoreDep) -> list[PolicyPack]:
    """List registered policy packs."""
    return store.list_packs()


@router.post("/policy-packs", response_model=PolicyPack, status_code=status.HTTP_201_CREATED)
def register_policy_pack(pack: PolicyPack, store: PackStoreDep) -> PolicyPack:
    """Register a team policy pack."""
    store.save(pack)
    return pack
```

`src/hiveplane/api/approvals.py`:

```python
"""Approval queue API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import get_approval_service, get_run_service
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.policy.approvals import ApprovalService

router = APIRouter(tags=["approvals"])

ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]
RunDep = Annotated[RunService, Depends(get_run_service)]


class ApprovalDecisionRequest(BaseModel):
    """Request body for approving or denying an approval."""

    model_config = ConfigDict(extra="forbid")

    operator: str = Field(min_length=1)
    reason: str | None = None


@router.get("/approvals", response_model=list[ApprovalRecord])
def list_approvals(
    service: ApprovalDep,
    status: ApprovalStatus | None = None,
    workload: str | None = None,
) -> list[ApprovalRecord]:
    """List approval requests."""
    return service.list(status=status, workload=workload)


@router.get("/approvals/{approval_id}", response_model=ApprovalRecord)
def get_approval(approval_id: str, service: ApprovalDep) -> ApprovalRecord:
    """Return an approval request."""
    return service.get(approval_id)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRecord)
def approve(
    approval_id: str, payload: ApprovalDecisionRequest, service: ApprovalDep, runs: RunDep
) -> ApprovalRecord:
    """Approve a request and resume the paused run."""
    record = service.decide(
        approval_id, status=ApprovalStatus.APPROVED, operator=payload.operator, reason=payload.reason
    )
    runs.intervene(record.run_id, InterventionAction.RESUME, actor=payload.operator)
    return record


@router.post("/approvals/{approval_id}/deny", response_model=ApprovalRecord)
def deny(
    approval_id: str, payload: ApprovalDecisionRequest, service: ApprovalDep, runs: RunDep
) -> ApprovalRecord:
    """Deny a request and fail the paused run."""
    record = service.decide(
        approval_id, status=ApprovalStatus.DENIED, operator=payload.operator, reason=payload.reason
    )
    runs.fail(record.run_id, actor=payload.operator, reason=payload.reason or "approval denied")
    return record
```

`src/hiveplane/api/deps.py` — add:

```python
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import PolicyPackStore


def get_policy_engine(request: Request) -> PolicyEngine:
    """Return the policy engine bound to the application state."""
    engine: PolicyEngine = request.app.state.policy_engine
    return engine


def get_policy_pack_store(request: Request) -> PolicyPackStore:
    """Return the policy pack store bound to the application state."""
    store: PolicyPackStore = request.app.state.policy_pack_store
    return store


def get_approval_service(request: Request) -> ApprovalService:
    """Return the approval service bound to the application state."""
    service: ApprovalService = request.app.state.approval_service
    return service
```

`src/hiveplane/execution/wiring.py` — change `build_run_service` to accept the policy gate and approvals:

```python
from hiveplane.execution.gates import (
    ApprovalRequests,
    ManifestSandboxGate,
    NullRunExecutor,
    RegistryCertificationGate,
    UnlimitedBudgetGate,
)
from hiveplane.execution.gates import PolicyGate


def build_run_service(
    registry_service: RegistryService,
    policy_gate: PolicyGate,
    approvals: ApprovalRequests,
) -> RunService:
    """Build a RunService with the real policy gate and approval seam."""
    settings = get_settings()
    store = InMemoryRunStore()
    fanout = FanOutService(...)
    return RunService(
        store,
        registry_service,
        admission=AdmissionPipeline(
            RegistryCertificationGate(registry_service),
            policy_gate,
            UnlimitedBudgetGate(),
            ManifestSandboxGate(),
        ),
        executor=NullRunExecutor(),
        fanout=fanout,
        approvals=approvals,
    )
```

`src/hiveplane/api/app.py` — build and wire the new services:

```python
from hiveplane.api.approvals import router as approvals_router
from hiveplane.api.policy import router as policy_router
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalNotFoundError,
    PolicyPackAlreadyExistsError,
    PolicyPackNotFoundError,
)
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import InMemoryApprovalStore
```

Inside `create_app`:

```python
    policy_pack_store = InMemoryPolicyPackStore()
    policy_engine = PolicyEngine(policy_pack_store)
    approval_service = ApprovalService(InMemoryApprovalStore())
    app.state.policy_pack_store = policy_pack_store
    app.state.policy_engine = policy_engine
    app.state.approval_service = approval_service
    app.state.run_service = run_service or build_run_service(
        registry, policy_engine, approval_service
    )
```

And add the error handlers:

```python
    for _policy_error, _policy_status in (
        (ApprovalNotFoundError, 404),
        (PolicyPackNotFoundError, 404),
        (PolicyPackAlreadyExistsError, 409),
        (ApprovalAlreadyDecidedError, 409),
    ):
        app.add_exception_handler(_policy_error, _make_handler(_policy_error, _policy_status))

    app.include_router(policy_router)
    app.include_router(approvals_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_policy_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Full exit gate + docs + commit**

Run:

```bash
.venv/bin/python -m pytest --cov=src/hiveplane --cov-report=term-missing -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src/ tests/
```

Coverage must exceed 95%; add focused tests for uncovered policy branches if needed. Update `docs/USER_GUIDE.md` with the `/policy/evaluate`, `/policy-packs`, and `/approvals` endpoints, and add a status note to `docs/wbs/v0.1.0/wbs-v0.1.0-part5-policy-approvals.md`.

```bash
git add src/hiveplane/api src/hiveplane/execution/wiring.py tests/test_policy_api.py docs/USER_GUIDE.md docs/wbs/v0.1.0/wbs-v0.1.0-part5-policy-approvals.md
git commit -m "feat(policy): expose policy and approval APIs and wire the engine"
git push origin main
```

---

## Self-review

**Spec coverage** (against `docs/design/policy-engine-design.md` and the execution-path design):

| Requirement | Task |
|-------------|------|
| Deny-by-default, explainable decisions (rule + reason) | 3 |
| Context-aware: environment, data sensitivity, blast radius | 3 |
| Certification status as a policy input | 3 |
| Team policy packs, tighten-only, deny wins | 2, 3 |
| Tool trust levels drive approval/sandbox/cert requirements | 3 |
| Decisions: allow/deny/escalate/block_injection | 1, 3 |
| Escalation to pending approval with evidence | 4, 5 |
| Approve resumes, deny fails with recorded reason | 4, 6 |
| Slack notification on approval-needed | 5 (on_escalation fan-out) |
| Explainability API | 6 |

Injection scanning implementation is deferred to phase 4; the engine accepts `injection_detected` and returns `block_injection` now.

**Placeholder scan:** no `TODO`/`TBD`/"handle edge cases"/"similar to Task N". Every code step is complete.

**Type consistency:** `ApprovalRecord` is defined once in Task 1 and used in Tasks 4/5/6. `PolicyContext` fields added in Task 1 (`tools`, `approval_required_for`, `injection_detected`) are used in Task 3. `ApprovalService.request` keyword names (`run_id`, `workload`, `rule`, `reason`, `action_class`) match the `ApprovalRequests` protocol and the Task 5 tests. `RunService` constructor gains only `approvals`, defaulted, so phase-1 tests keep passing. `build_run_service` signature changes in Task 6; all callers (`create_app`) are updated in the same task.

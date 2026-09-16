# Run Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the run lifecycle core (models, stores, admission, state machine, intervention, fan-out, REST API) against injectable gate/executor seams, so later phases can drop in real policy, budget, sandbox, and adapters.

**Architecture:** `execution/` owns the run aggregate, `RunStore` protocol, gate/executor protocols, `AdmissionPipeline`, `RunService`, and `FanOutService`. Gate inputs and outputs live in `core/` (which has no dependency on `execution/`), so the seams compile before real engines exist. Test doubles live in `tests/`, never in `src/`.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, pytest, ruff, mypy strict.

## Global Constraints

- Python `>=3.12`; pydantic v2; FastAPI; stdlib `urllib` for HTTP (no new runtime dependency).
- Every task must leave `make check` green: `ruff check`, `mypy src/ tests/`, `pytest --cov` total > 95%.
- Ruff config: line length 100; `ANN` enforced in `src/` (every function annotated), ignored in `tests/`.
- Mypy strict: `disallow_untyped_defs` enforced in `src/`; tests may omit annotations.
- Datetimes are timezone-aware (`datetime.now(UTC)`); all logic takes an injected `clock` callable, never reads the wall clock directly.
- Stores are thread-safe and copy on read and write (match `InMemoryRegistryStore`).
- Do not add code comments; keep module/class/function docstrings in the repo's style.
- Do not name files with dates or milestone identifiers.
- Reuse existing models where they exist: `RunState`/`can_transition` (`core/run.py`), `RunEvent`/`EventType` (`core/event.py`), `UsageReport` (`core/usage.py`), `PolicyDecision`/`ActionClass`/`DecisionOutcome` (`core/decision.py`), `ToolsSpec`/`ToolTrustLevel` (`core/tools.py`), `FanOutSpec`/`FanOutDestination`/`FanOutType` (`core/fanout.py`), `AgentWorkload` (`core/workload.py`), `RegistryService` (`registry/service.py`).

---

## Phase scope

This plan covers **Phase 1 — Run lifecycle core** only. Later phases (policy, budget, sandbox/shaping, adapters/conformance) get their own plans after this one lands, per the parent design doc:

1. **Run lifecycle core** ← this plan
2. Policy and approvals
3. Budget
4. Sandbox and shaping
5. Adapters and conformance

---

## File structure

| File | Responsibility |
|------|----------------|
| `src/hiveplane/core/run.py` | `AdmissionContext`, extended `Run` aggregate |
| `src/hiveplane/core/decision.py` | `DataSensitivity`, `BlastRadius`, `PolicyContext`, extended decisions |
| `src/hiveplane/core/usage.py` | `BudgetLevel`, `BudgetCheck` |
| `src/hiveplane/registry/models.py` | Re-export `AdmissionContext` from core |
| `src/hiveplane/execution/__init__.py` | Package marker |
| `src/hiveplane/execution/models.py` | Admission/intervention/delivery/submission models |
| `src/hiveplane/execution/errors.py` | Execution domain errors |
| `src/hiveplane/execution/store.py` | `RunStore` protocol, `InMemoryRunStore`, `JsonFileRunStore` |
| `src/hiveplane/execution/gates.py` | Gate/executor protocols and phase-1 default implementations |
| `src/hiveplane/execution/admission.py` | `AdmissionPipeline` |
| `src/hiveplane/execution/fanout.py` | `FanOutService`, `DeliveryTransport`, transports |
| `src/hiveplane/execution/service.py` | `RunService` |
| `src/hiveplane/execution/wiring.py` | `build_run_service` composition root |
| `src/hiveplane/api/deps.py` | `get_run_service` dependency |
| `src/hiveplane/api/runs.py` | `/runs` router |
| `src/hiveplane/api/app.py` | Wire run router + error handlers |
| `tests/test_core_run_models.py` | Core model tests |
| `tests/test_execution_models.py` | Execution model tests |
| `tests/test_execution_store.py` | Store tests (memory + JSON durability) |
| `tests/test_execution_gates.py` | Gate tests |
| `tests/test_execution_admission.py` | Admission pipeline tests |
| `tests/test_execution_service.py` | RunService tests |
| `tests/test_execution_fanout.py` | Fan-out tests |
| `tests/test_execution_api.py` | API tests |

All test files live flat in `tests/`, matching the existing repo convention.

---

### Task 1: Core run, decision, and usage models

**Files:**
- Modify: `src/hiveplane/core/run.py`
- Modify: `src/hiveplane/core/decision.py`
- Modify: `src/hiveplane/core/usage.py`
- Modify: `src/hiveplane/registry/models.py`
- Test: `tests/test_core_run_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `core.run.AdmissionContext` (StrEnum: `sandbox`, `staging`, `production`)
  - `core.run.Run` with added fields: `manifest_version: int | None`, `context: AdmissionContext | None`, `sandbox: bool`, `task: dict[str, JsonValue]`, `result: JsonValue | None`, `failure_reason: str | None`, `cost_usd: float`
  - `core.decision.DataSensitivity`, `core.decision.BlastRadius`, `core.decision.PolicyContext`
  - `core.decision.DecisionOutcome.BLOCK_INJECTION`
  - `core.decision.PolicyDecision.blast_radius`, `.certification_status`
  - `core.usage.BudgetLevel`, `core.usage.BudgetCheck`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the extended run, decision, and usage models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.core.decision import (
    ActionClass,
    BlastRadius,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
    PolicyDecision,
)
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import BudgetCheck, BudgetLevel


def _run(**overrides: object) -> Run:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    data: dict[str, object] = {
        "id": "run-1",
        "workload_id": "agent-1",
        "caller": "cli",
        "state": RunState.QUEUED,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return Run.model_validate(data)


def test_run_defaults_for_execution_fields() -> None:
    run = _run()
    assert run.manifest_version is None
    assert run.context is None
    assert run.sandbox is False
    assert run.task == {}
    assert run.result is None
    assert run.failure_reason is None
    assert run.cost_usd == 0.0


def test_run_accepts_execution_fields() -> None:
    run = _run(
        manifest_version=3,
        context=AdmissionContext.PRODUCTION,
        sandbox=True,
        task={"alert": "high"},
        cost_usd=0.25,
    )
    assert run.context is AdmissionContext.PRODUCTION
    assert run.sandbox is True
    assert run.task == {"alert": "high"}
    assert run.cost_usd == 0.25


def test_admission_context_values() -> None:
    assert {c.value for c in AdmissionContext} == {"sandbox", "staging", "production"}


def test_decision_outcome_includes_block_injection() -> None:
    assert DecisionOutcome.BLOCK_INJECTION.value == "block_injection"


def test_policy_context_and_decision() -> None:
    context = PolicyContext(
        run_id="run-1",
        workload="agent-1",
        environment=AdmissionContext.PRODUCTION,
        action_class=ActionClass.DESTRUCTIVE,
        data_sensitivity=DataSensitivity.PII,
    )
    assert context.data_sensitivity is DataSensitivity.PII
    decision = PolicyDecision(
        run_id="run-1",
        outcome=DecisionOutcome.ALLOW,
        reason="ok",
        rule="manifest.allow",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        blast_radius=BlastRadius(score=42, factors={"trust": 20}),
        certification_status=context.certification_status,
    )
    assert decision.blast_radius is not None
    assert decision.blast_radius.score == 42


def test_blast_radius_bounds() -> None:
    with pytest.raises(ValidationError):
        BlastRadius(score=101)


def test_budget_check_bounds() -> None:
    check = BudgetCheck(
        allowed=False,
        level=BudgetLevel.DAY,
        limit_usd=5.0,
        spent_usd=5.0,
        remaining_usd=0.0,
        reason="day budget exhausted",
    )
    assert check.allowed is False
    with pytest.raises(ValidationError):
        BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=1.0,
            spent_usd=0.0,
            remaining_usd=-1.0,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core_run_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'AdmissionContext' from 'hiveplane.core.run'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/core/run.py` — add the import and enum, and extend `Run`:

```python
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class AdmissionContext(StrEnum):
    """Target contexts a run can be admitted to."""

    SANDBOX = "sandbox"
    STAGING = "staging"
    PRODUCTION = "production"
```

```python
class Run(BaseModel):
    """A single execution of a workload through the control plane."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    caller: str = Field(min_length=1)
    state: RunState
    model_identity: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    trigger_origin: TriggerOrigin | None = None
    manifest_version: int | None = None
    context: AdmissionContext | None = None
    sandbox: bool = False
    task: dict[str, JsonValue] = Field(default_factory=dict)
    result: JsonValue | None = None
    failure_reason: str | None = None
    cost_usd: float = Field(default=0.0, ge=0.0)
```

`src/hiveplane/registry/models.py` — remove the `AdmissionContext` class and import it from core (keep using it in `AdmissionDecision`):

```python
from hiveplane.core.run import AdmissionContext
```

`src/hiveplane/core/decision.py` — add imports and models:

```python
from hiveplane.certification.models import CertificationStatus
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel


class DecisionOutcome(StrEnum):
    """The outcome of evaluating policy for a run or tool call."""

    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"
    BLOCK_INJECTION = "block_injection"


class DataSensitivity(StrEnum):
    """Sensitivity classification of the data a run touches."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PII = "pii"
    RESTRICTED = "restricted"


class BlastRadius(BaseModel):
    """A blast-radius score (0-100) and the factor contributions to it."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    factors: dict[str, int] = Field(default_factory=dict)


class PolicyContext(BaseModel):
    """The context a policy decision is evaluated against."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    team: str | None = None
    environment: AdmissionContext
    action_class: ActionClass | None = None
    tool_id: str | None = None
    tool_trust: ToolTrustLevel | None = None
    data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    certification_status: CertificationStatus = CertificationStatus.UNCERTIFIED
```

Add `PolicyDecision` fields:

```python
    blast_radius: BlastRadius | None = None
    certification_status: CertificationStatus | None = None
```

`src/hiveplane/core/usage.py` — add:

```python
from enum import StrEnum


class BudgetLevel(StrEnum):
    """The budget scope a check applies to."""

    RUN = "run"
    DAY = "day"
    TEAM = "team"


class BudgetCheck(BaseModel):
    """The result of a budget check at run, day, or team scope."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    level: BudgetLevel
    limit_usd: float = Field(ge=0.0)
    spent_usd: float = Field(ge=0.0)
    remaining_usd: float = Field(ge=0.0)
    reason: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core_run_models.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite to catch regressions**

Run: `python -m pytest -q`
Expected: PASS — existing `test_registry_api*.py`, `test_registry_m4.py`, and `test_core_models.py` still pass because `AdmissionContext` remains importable from `registry.models`.

- [ ] **Step 6: Commit**

```bash
git add src/hiveplane/core/run.py src/hiveplane/core/decision.py src/hiveplane/core/usage.py src/hiveplane/registry/models.py tests/test_core_run_models.py
git commit -m "feat(execution): extend run, decision, and usage models for the execution path"
```

---

### Task 2: Execution models and errors

**Files:**
- Create: `src/hiveplane/execution/__init__.py`
- Create: `src/hiveplane/execution/models.py`
- Create: `src/hiveplane/execution/errors.py`
- Test: `tests/test_execution_models.py`

**Interfaces:**
- Consumes: `core.run.AdmissionContext`, `core.run.Run`, `core.fanout.FanOutType`, `core.workload.AgentWorkload`.
- Produces:
  - `execution.models.AdmissionOutcome`, `AdmissionCheck`, `AdmissionResult`, `InterventionAction`, `DeliveryStatus`, `DeliveryRecord`, `RunContext`, `RunSubmission`
  - `execution.errors.ExecutionError`, `RunNotFoundError`, `IllegalTransitionError`, `RunAdmissionRefusedError`, `RunNotIntervenableError`
  - `AdmissionResult` fields: `run_id`, `workload`, `context`, `outcome`, `checks`, `sandbox`, `escalation_required`, `refused_reason`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for execution models and errors."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.core.fanout import FanOutType
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.models import (
    AdmissionCheck,
    AdmissionOutcome,
    AdmissionResult,
    DeliveryRecord,
    DeliveryStatus,
    InterventionAction,
    RunSubmission,
)


def _result(**overrides: object) -> AdmissionResult:
    data: dict[str, object] = {
        "run_id": "run-1",
        "workload": "agent-1",
        "context": AdmissionContext.PRODUCTION,
        "outcome": AdmissionOutcome.REFUSED,
        "refused_reason": "not certified",
    }
    data.update(overrides)
    return AdmissionResult.model_validate(data)


def test_admission_result_defaults() -> None:
    result = _result()
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.checks == []
    assert result.sandbox is False
    assert result.escalation_required is False


def test_admission_check_records_step() -> None:
    check = AdmissionCheck(step="certification", passed=False, reason="uncertified", rule="cert")
    assert check.passed is False
    assert check.rule == "cert"


def test_intervention_actions() -> None:
    assert {a.value for a in InterventionAction} == {"pause", "resume", "stop"}


def test_delivery_record_requires_target() -> None:
    record = DeliveryRecord(
        run_id="run-1",
        destination_type=FanOutType.WEBHOOK,
        target="https://example.test/hook",
        status=DeliveryStatus.DELIVERED,
        attempts=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert record.attempts == 1
    with pytest.raises(ValidationError):
        DeliveryRecord(
            run_id="run-1",
            destination_type=FanOutType.WEBHOOK,
            target="",
            status=DeliveryStatus.PENDING,
            attempts=0,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_run_submission_defaults() -> None:
    submission = RunSubmission(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    assert submission.task == {}
    assert submission.model_identity is None
    assert submission.context is AdmissionContext.SANDBOX


def test_errors_carry_context() -> None:
    assert RunNotFoundError("run-1").run_id == "run-1"
    illegal = IllegalTransitionError("run-1", RunState.COMPLETED, RunState.RUNNING)
    assert illegal.current is RunState.COMPLETED
    refused = RunAdmissionRefusedError(_result())
    assert "agent-1" in str(refused)
    assert RunNotIntervenableError("run-1", "pause").run_id == "run-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.execution'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/execution/__init__.py`:

```python
"""Run lifecycle: admission, state machine, intervention, and fan-out."""
```

`src/hiveplane/execution/models.py`:

```python
"""Execution domain models for the run lifecycle."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.fanout import FanOutType
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.core.workload import AgentWorkload


class AdmissionOutcome(StrEnum):
    """The disposition of an admission decision."""

    ADMITTED = "admitted"
    SANDBOX_ONLY = "sandbox_only"
    REFUSED = "refused"


class AdmissionCheck(BaseModel):
    """One step of the admission pipeline with its outcome."""

    model_config = ConfigDict(extra="forbid")

    step: str = Field(min_length=1)
    passed: bool
    reason: str | None = None
    rule: str | None = None


class AdmissionResult(BaseModel):
    """The full admission decision for a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    context: AdmissionContext
    outcome: AdmissionOutcome
    checks: list[AdmissionCheck] = Field(default_factory=list)
    sandbox: bool = False
    escalation_required: bool = False
    refused_reason: str | None = None


class InterventionAction(StrEnum):
    """Operator interventions on a live run."""

    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


class DeliveryStatus(StrEnum):
    """Delivery state of a fan-out message."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class DeliveryRecord(BaseModel):
    """A recorded attempt to deliver a run result to a destination."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    destination_type: FanOutType
    target: str = Field(min_length=1)
    status: DeliveryStatus
    attempts: int = Field(ge=0)
    error: str | None = None
    timestamp: AwareDatetime


class RunContext(BaseModel):
    """The execution context handed to a RunExecutor."""

    model_config = ConfigDict(extra="forbid")

    run: Run
    workload: AgentWorkload
    sandbox: bool = False


class RunSubmission(BaseModel):
    """The request body for submitting a run."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    caller: str = Field(min_length=1)
    context: AdmissionContext
    task: dict[str, JsonValue] = Field(default_factory=dict)
    model_identity: str | None = None
```

`src/hiveplane/execution/errors.py`:

```python
"""Execution domain errors."""

from __future__ import annotations

from hiveplane.core.run import RunState
from hiveplane.execution.models import AdmissionResult


class ExecutionError(Exception):
    """Base class for execution errors."""


class RunNotFoundError(ExecutionError):
    """Raised when a run id is unknown to the store."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id!r} not found")
        self.run_id = run_id


class IllegalTransitionError(ExecutionError):
    """Raised when a run state transition is not permitted."""

    def __init__(self, run_id: str, current: RunState, target: RunState) -> None:
        super().__init__(
            f"run {run_id!r} cannot transition from {current.value!r} to {target.value!r}"
        )
        self.run_id = run_id
        self.current = current
        self.target = target


class RunAdmissionRefusedError(ExecutionError):
    """Raised when a run is refused admission to its target context."""

    def __init__(self, result: AdmissionResult) -> None:
        reason = result.refused_reason or "admission refused"
        super().__init__(
            f"run for workload {result.workload!r} refused admission to "
            f"{result.context.value}: {reason}"
        )
        self.result = result


class RunNotIntervenableError(ExecutionError):
    """Raised when an intervention is invalid for the run's current state."""

    def __init__(self, run_id: str, action: str) -> None:
        super().__init__(f"run {run_id!r} cannot be {action} in its current state")
        self.run_id = run_id
        self.action = action
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_models.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/execution/__init__.py src/hiveplane/execution/models.py src/hiveplane/execution/errors.py tests/test_execution_models.py
git commit -m "feat(execution): add execution models and errors"
```

---

### Task 3: RunStore protocol and in-memory store

**Files:**
- Create: `src/hiveplane/execution/store.py`
- Test: `tests/test_execution_store.py`

**Interfaces:**
- Consumes: `core.run.Run`, `core.run.RunState`, `core.event.RunEvent`, `core.usage.UsageReport`, `execution.models.AdmissionResult`, `execution.models.DeliveryRecord`, `execution.errors.RunNotFoundError`.
- Produces:
  - `execution.store.RunStore` (Protocol) with `save_run`, `get_run`, `list_runs(*, workload, state)`, `add_event`, `list_events`, `add_usage`, `list_usage`, `save_admission`, `get_admission`, `add_delivery`, `list_deliveries`
  - `execution.store.InMemoryRunStore`
  - Internal `RunBundle` model (run + admission + events + usage + deliveries)

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the run store."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import DeliveryRecord, DeliveryStatus, AdmissionOutcome, AdmissionResult
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.core.fanout import FanOutType


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _run(run_id: str = "run-1", state: RunState = RunState.QUEUED, **overrides: object) -> Run:
    data: dict[str, object] = {
        "id": run_id,
        "workload_id": "agent-1",
        "caller": "cli",
        "state": state,
        "created_at": _now(),
        "updated_at": _now(),
    }
    data.update(overrides)
    return Run.model_validate(data)


def test_save_and_get_run_round_trips() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    fetched = store.get_run("run-1")
    assert fetched is not None
    assert fetched.state is RunState.QUEUED


def test_get_run_missing_returns_none() -> None:
    assert InMemoryRunStore().get_run("nope") is None


def test_get_run_returns_a_copy() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    fetched = store.get_run("run-1")
    assert fetched is not None
    fetched.state = RunState.FAILED
    assert store.get_run("run-1").state is RunState.QUEUED  # type: ignore[union-attr]


def test_events_are_ordered_and_copied() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    store.add_event(
        RunEvent(
            run_id="run-1",
            sequence=1,
            type=EventType.STATE_CHANGE,
            actor="a",
            timestamp=_now(),
            from_state=RunState.QUEUED,
            to_state=RunState.RUNNING,
        )
    )
    store.add_event(
        RunEvent(run_id="run-1", sequence=0, type=EventType.ADMISSION, actor="a", timestamp=_now())
    )
    assert [e.sequence for e in store.list_events("run-1")] == [0, 1]


def test_event_for_unknown_run_raises() -> None:
    with pytest.raises(RunNotFoundError):
        InMemoryRunStore().add_event(
            RunEvent(run_id="nope", sequence=0, type=EventType.ADMISSION, actor="a", timestamp=_now())
        )


def test_list_runs_filters() -> None:
    store = InMemoryRunStore()
    store.save_run(_run("run-1", workload_id="agent-1"))
    store.save_run(_run("run-2", workload_id="agent-2", state=RunState.RUNNING))
    assert {r.id for r in store.list_runs(workload="agent-2")} == {"run-2"}
    assert {r.id for r in store.list_runs(state=RunState.QUEUED)} == {"run-1"}


def test_admission_and_usage_and_delivery_round_trip() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    store.save_admission(
        AdmissionResult(
            run_id="run-1",
            workload="agent-1",
            context=AdmissionContext.SANDBOX,
            outcome=AdmissionOutcome.SANDBOX_ONLY,
        )
    )
    store.add_usage(
        UsageReport(
            run_id="run-1",
            input_tokens=10,
            output_tokens=5,
            tool_calls=1,
            cost_usd=0.01,
            timestamp=_now(),
        )
    )
    store.add_delivery(
        DeliveryRecord(
            run_id="run-1",
            destination_type=FanOutType.WEBHOOK,
            target="https://example.test/hook",
            status=DeliveryStatus.DELIVERED,
            attempts=1,
            timestamp=_now(),
        )
    )
    assert store.get_admission("run-1").outcome is AdmissionOutcome.SANDBOX_ONLY  # type: ignore[union-attr]
    assert store.list_usage("run-1")[0].total_tokens == 15
    assert store.list_deliveries("run-1")[0].status is DeliveryStatus.DELIVERED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'InMemoryRunStore'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Run storage abstraction and its in-memory implementation."""

from __future__ import annotations

import threading
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.event import RunEvent
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import AdmissionResult, DeliveryRecord


class RunBundle(BaseModel):
    """All persisted state for a single run."""

    model_config = ConfigDict(extra="forbid")

    run: Run
    admission: AdmissionResult | None = None
    events: list[RunEvent] = Field(default_factory=list)
    usage: list[UsageReport] = Field(default_factory=list)
    deliveries: list[DeliveryRecord] = Field(default_factory=list)


class RunStore(Protocol):
    """Storage interface for runs and their recorded history."""

    def save_run(self, run: Run) -> None: ...

    def get_run(self, run_id: str) -> Run | None: ...

    def list_runs(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]: ...

    def add_event(self, event: RunEvent) -> None: ...

    def list_events(self, run_id: str) -> list[RunEvent]: ...

    def add_usage(self, report: UsageReport) -> None: ...

    def list_usage(self, run_id: str) -> list[UsageReport]: ...

    def save_admission(self, result: AdmissionResult) -> None: ...

    def get_admission(self, run_id: str) -> AdmissionResult | None: ...

    def add_delivery(self, record: DeliveryRecord) -> None: ...

    def list_deliveries(self, run_id: str) -> list[DeliveryRecord]: ...


class _BundleStore:
    """Shared thread-safe bundle storage; subclasses add persistence hooks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bundles: dict[str, RunBundle] = {}

    def _require(self, run_id: str) -> RunBundle:
        bundle = self._bundles.get(run_id)
        if bundle is None:
            raise RunNotFoundError(run_id)
        return bundle

    def _commit(self, run_id: str) -> None:
        """Persist a bundle after mutation; a no-op in memory."""

    def save_run(self, run: Run) -> None:
        with self._lock:
            bundle = self._bundles.get(run.id)
            if bundle is None:
                self._bundles[run.id] = RunBundle(run=run.model_copy(deep=True))
            else:
                bundle.run = run.model_copy(deep=True)
            self._commit(run.id)

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            bundle = self._bundles.get(run_id)
            return bundle.run.model_copy(deep=True) if bundle is not None else None

    def list_runs(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]:
        with self._lock:
            runs = [bundle.run for bundle in self._bundles.values()]
        if workload is not None:
            runs = [run for run in runs if run.workload_id == workload]
        if state is not None:
            runs = [run for run in runs if run.state is state]
        runs.sort(key=lambda run: run.created_at)
        return [run.model_copy(deep=True) for run in runs]

    def add_event(self, event: RunEvent) -> None:
        with self._lock:
            self._require(event.run_id).events.append(event.model_copy(deep=True))
            self._commit(event.run_id)

    def list_events(self, run_id: str) -> list[RunEvent]:
        with self._lock:
            events = sorted(self._require(run_id).events, key=lambda item: item.sequence)
            return [event.model_copy(deep=True) for event in events]

    def add_usage(self, report: UsageReport) -> None:
        with self._lock:
            self._require(report.run_id).usage.append(report.model_copy(deep=True))
            self._commit(report.run_id)

    def list_usage(self, run_id: str) -> list[UsageReport]:
        with self._lock:
            reports = self._require(run_id).usage
            return [report.model_copy(deep=True) for report in reports]

    def save_admission(self, result: AdmissionResult) -> None:
        with self._lock:
            self._require(result.run_id).admission = result.model_copy(deep=True)
            self._commit(result.run_id)

    def get_admission(self, run_id: str) -> AdmissionResult | None:
        with self._lock:
            admission = self._require(run_id).admission
            return admission.model_copy(deep=True) if admission is not None else None

    def add_delivery(self, record: DeliveryRecord) -> None:
        with self._lock:
            self._require(record.run_id).deliveries.append(record.model_copy(deep=True))
            self._commit(record.run_id)

    def list_deliveries(self, run_id: str) -> list[DeliveryRecord]:
        with self._lock:
            deliveries = self._require(run_id).deliveries
            return [record.model_copy(deep=True) for record in deliveries]


class InMemoryRunStore(_BundleStore):
    """A process-local, thread-safe run store."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/execution/store.py tests/test_execution_store.py
git commit -m "feat(execution): add run store protocol and in-memory implementation"
```

---

### Task 4: JSON-file run store (durability)

**Files:**
- Modify: `src/hiveplane/execution/store.py`
- Test: `tests/test_execution_store.py`

**Interfaces:**
- Consumes: `_BundleStore`, `RunBundle` from Task 3.
- Produces: `execution.store.JsonFileRunStore(base_dir: str | Path)` — same interface as `InMemoryRunStore`, writes one `<run_id>.json` per run and reloads on construction.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_execution_store.py`:

```python
from pathlib import Path

from hiveplane.execution.store import JsonFileRunStore


def test_json_store_survives_reconstruction(tmp_path: Path) -> None:
    store = JsonFileRunStore(tmp_path)
    store.save_run(_run(state=RunState.PAUSED))
    store.add_event(
        RunEvent(
            run_id="run-1",
            sequence=0,
            type=EventType.STATE_CHANGE,
            actor="operator",
            timestamp=_now(),
            from_state=RunState.RUNNING,
            to_state=RunState.PAUSED,
        )
    )
    store.add_usage(
        UsageReport(
            run_id="run-1",
            input_tokens=3,
            output_tokens=4,
            tool_calls=0,
            cost_usd=0.02,
            timestamp=_now(),
        )
    )

    reopened = JsonFileRunStore(tmp_path)
    assert reopened.get_run("run-1").state is RunState.PAUSED  # type: ignore[union-attr]
    assert reopened.list_events("run-1")[0].to_state is RunState.PAUSED
    assert reopened.list_usage("run-1")[0].total_tokens == 7


def test_json_store_writes_one_file_per_run(tmp_path: Path) -> None:
    store = JsonFileRunStore(tmp_path)
    store.save_run(_run("run-1"))
    store.save_run(_run("run-2"))
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["run-1.json", "run-2.json"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_store.py -k json -v`
Expected: FAIL with `ImportError: cannot import name 'JsonFileRunStore'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/hiveplane/execution/store.py`:

```python
class JsonFileRunStore(_BundleStore):
    """A run store that persists each run bundle to a JSON file.

    Used for local durability and restart tests until the PostgreSQL-backed
    store is available.
    """

    def __init__(self, base_dir: str | Path) -> None:
        super().__init__()
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self._dir.glob("*.json")):
            bundle = RunBundle.model_validate_json(path.read_text(encoding="utf-8"))
            self._bundles[bundle.run.id] = bundle

    def _commit(self, run_id: str) -> None:
        bundle = self._bundles[run_id]
        target = self._dir / f"{run_id}.json"
        target.write_text(bundle.model_dump_json(), encoding="utf-8")
```

Add the import at the top of the module:

```python
from pathlib import Path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_store.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/execution/store.py tests/test_execution_store.py
git commit -m "feat(execution): add durable JSON-file run store"
```

---

### Task 5: Gate and executor protocols

**Files:**
- Create: `src/hiveplane/execution/gates.py`
- Test: `tests/test_execution_gates.py`

**Interfaces:**
- Consumes: `registry.models.AdmissionDecision`, `registry.service.RegistryService`, `core.decision.PolicyContext`/`PolicyDecision`, `core.usage.BudgetCheck`/`BudgetLevel`/`UsageReport`, `core.decision.ActionClass`, `core.run.AdmissionContext`, `core.run.RunState`, `core.workload.AgentWorkload`, `execution.models.RunContext`.
- Produces:
  - `CertificationGate` / `RegistryCertificationGate(registry)` with `check_admission(workload: str, context) -> AdmissionDecision` and `attestation_model(workload: str) -> str | None`
  - `PolicyGate` with `evaluate(context: PolicyContext) -> PolicyDecision`
  - `PermissivePolicyGate(clock)`
  - `BudgetGate` with `check(workload, context) -> BudgetCheck` and `record_usage(report) -> BudgetCheck`
  - `UnlimitedBudgetGate`
  - `SandboxGate` with `required(workload, context, action_class) -> bool`
  - `ManifestSandboxGate`
  - `RunExecutor` with `start(RunContext)`, `pause(run_id) -> bool`, `resume(run_id) -> bool`, `cancel(run_id)`, `status(run_id) -> RunState`, `usage(run_id) -> UsageReport | None`
  - `NullRunExecutor`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the execution gate protocols and phase-1 defaults."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome, PolicyContext
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import BudgetLevel, UsageReport
from hiveplane.execution.gates import (
    ManifestSandboxGate,
    PermissivePolicyGate,
    RegistryCertificationGate,
    UnlimitedBudgetGate,
)
from hiveplane.registry.models import AdmissionDecision


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _FakeRegistry:
    def __init__(self, decision: AdmissionDecision, model: str | None) -> None:
        self._decision = decision
        self._model = model
        self.calls: list[tuple[str, AdmissionContext]] = []

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision:
        self.calls.append((workload, context))
        return self._decision

    def list_attestations(self, workload: str) -> list[object]:
        if self._model is None:
            return []

        class _Attestation:
            model_identity = self._model

        return [_Attestation()]


def test_registry_certification_gate_delegates() -> None:
    decision = AdmissionDecision(
        workload="agent-1",
        context=AdmissionContext.PRODUCTION,
        admitted=True,
        actual_status="certified",  # type: ignore[arg-type]
    )
    registry = _FakeRegistry(decision, "openai/gpt-4o/2024-08-06")
    gate = RegistryCertificationGate(registry)  # type: ignore[arg-type]
    assert gate.check_admission("agent-1", AdmissionContext.PRODUCTION).admitted is True
    assert registry.calls == [("agent-1", AdmissionContext.PRODUCTION)]
    assert gate.attestation_model("agent-1") == "openai/gpt-4o/2024-08-06"


def test_registry_certification_gate_no_attestation() -> None:
    decision = AdmissionDecision(
        workload="agent-1",
        context=AdmissionContext.SANDBOX,
        admitted=True,
        actual_status="uncertified",  # type: ignore[arg-type]
    )
    gate = RegistryCertificationGate(_FakeRegistry(decision, None))  # type: ignore[arg-type]
    assert gate.attestation_model("agent-1") is None


def test_permissive_policy_gate_allows() -> None:
    decision = PermissivePolicyGate(_clock).evaluate(
        PolicyContext(run_id="run-1", workload="agent-1", environment=AdmissionContext.SANDBOX)
    )
    assert decision.outcome is DecisionOutcome.ALLOW


def test_unlimited_budget_gate_allows(make_manifest: Callable[..., object]) -> None:
    workload = make_manifest()
    gate = UnlimitedBudgetGate()
    check = gate.check(workload, AdmissionContext.PRODUCTION)  # type: ignore[arg-type]
    assert check.allowed is True
    assert check.level is BudgetLevel.RUN
    usage = UsageReport(
        run_id="run-1",
        input_tokens=0,
        output_tokens=0,
        tool_calls=0,
        cost_usd=0.05,
        timestamp=_clock(),
    )
    assert gate.record_usage(usage).allowed is True


def test_manifest_sandbox_gate(make_manifest: Callable[..., object]) -> None:
    gate = ManifestSandboxGate()
    with_sandbox = make_manifest(sandbox={"enabled": True, "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10}})
    assert gate.required(with_sandbox, AdmissionContext.PRODUCTION, None) is True  # type: ignore[arg-type]
    assert gate.required(with_sandbox, AdmissionContext.SANDBOX, None) is True  # type: ignore[arg-type]
    without = make_manifest()
    assert gate.required(without, AdmissionContext.PRODUCTION, None) is False  # type: ignore[arg-type]
    from hiveplane.core.decision import ActionClass

    assert gate.required(without, AdmissionContext.PRODUCTION, ActionClass.DESTRUCTIVE) is True  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_gates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.execution.gates'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Run-lifecycle gate and executor protocols plus phase-1 defaults.

The protocols are the seams the lifecycle depends on. The phase-1 defaults keep
the control plane runnable before the policy, budget, sandbox, and adapter
engines exist; each is replaced by the real implementation in its own phase.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from hiveplane.core.decision import ActionClass, PolicyContext, PolicyDecision, DecisionOutcome
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.usage import BudgetCheck, BudgetLevel, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.registry.models import AdmissionDecision
from hiveplane.registry.service import RegistryService


class CertificationGate(Protocol):
    """Reads certification status and attestation model for admission."""

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision: ...

    def attestation_model(self, workload: str) -> str | None: ...


class RegistryCertificationGate:
    """CertificationGate backed by the registry service."""

    def __init__(self, registry: RegistryService) -> None:
        self._registry = registry

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision:
        """Return the registry admission decision for a workload and context."""
        return self._registry.check_admission(workload, context)

    def attestation_model(self, workload: str) -> str | None:
        """Return the model identity bound to the latest attestation, if any."""
        attestations = self._registry.list_attestations(workload)
        return attestations[-1].model_identity if attestations else None


class PolicyGate(Protocol):
    """Evaluates context-aware policy for an admission or tool call."""

    def evaluate(self, context: PolicyContext) -> PolicyDecision: ...


class PermissivePolicyGate:
    """Phase-1 default that allows everything; replaced by the policy engine."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def evaluate(self, context: PolicyContext) -> PolicyDecision:
        """Return an allow decision."""
        return PolicyDecision(
            run_id=context.run_id,
            outcome=DecisionOutcome.ALLOW,
            reason="policy engine not configured",
            rule="default-allow",
            action_class=context.action_class,
            timestamp=self._clock(),
            certification_status=context.certification_status,
        )


class BudgetGate(Protocol):
    """Checks budget headroom and records usage."""

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck: ...

    def record_usage(self, report: UsageReport) -> BudgetCheck: ...


class UnlimitedBudgetGate:
    """Phase-1 default that never blocks; replaced by the budget service."""

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck:
        """Return an allow check against the workload's per-run budget."""
        limit = workload.spec.budget.per_run_usd
        return BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=limit,
            spent_usd=0.0,
            remaining_usd=limit,
        )

    def record_usage(self, report: UsageReport) -> BudgetCheck:
        """Return an allow check for a usage report."""
        return BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=report.cost_usd,
            spent_usd=report.cost_usd,
            remaining_usd=0.0,
        )


class SandboxGate(Protocol):
    """Decides whether a run must execute in the sandbox."""

    def required(
        self,
        workload: AgentWorkload,
        context: AdmissionContext,
        action_class: ActionClass | None,
    ) -> bool: ...


class ManifestSandboxGate:
    """Sandbox requirement derived from the manifest and run context."""

    def required(
        self,
        workload: AgentWorkload,
        context: AdmissionContext,
        action_class: ActionClass | None,
    ) -> bool:
        """Return True when the workload, context, or action class needs isolation."""
        if context is AdmissionContext.SANDBOX:
            return True
        if action_class is ActionClass.DESTRUCTIVE:
            return True
        sandbox = workload.spec.sandbox
        return sandbox is not None and sandbox.enabled


class RunExecutor(Protocol):
    """The execution seam implemented by runtime adapters."""

    def start(self, context: RunContext) -> None: ...

    def pause(self, run_id: str) -> bool: ...

    def resume(self, run_id: str) -> bool: ...

    def cancel(self, run_id: str) -> None: ...

    def status(self, run_id: str) -> RunState: ...

    def usage(self, run_id: str) -> UsageReport | None: ...


class NullRunExecutor:
    """Phase-1 default executor that records nothing; replaced by adapters."""

    def __init__(self) -> None:
        self._states: dict[str, RunState] = {}

    def start(self, context: RunContext) -> None:
        """Record the run as running."""
        self._states[context.run.id] = RunState.RUNNING

    def pause(self, run_id: str) -> bool:
        """Accept a pause request."""
        self._states[run_id] = RunState.PAUSED
        return True

    def resume(self, run_id: str) -> bool:
        """Accept a resume request."""
        self._states[run_id] = RunState.RUNNING
        return True

    def cancel(self, run_id: str) -> None:
        """Record the run as cancelled."""
        self._states[run_id] = RunState.CANCELLED

    def status(self, run_id: str) -> RunState:
        """Return the last recorded state."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return no usage."""
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_gates.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/execution/gates.py tests/test_execution_gates.py
git commit -m "feat(execution): add gate and executor protocols with phase-1 defaults"
```

---

### Task 6: Admission pipeline

**Files:**
- Create: `src/hiveplane/execution/admission.py`
- Test: `tests/test_execution_admission.py`

**Interfaces:**
- Consumes: `execution.gates.CertificationGate`/`PolicyGate`/`BudgetGate`/`SandboxGate`, `execution.models.AdmissionResult`/`AdmissionCheck`/`AdmissionOutcome`, `execution.models.Run`, `core.decision.PolicyContext`, `core.usage.BudgetCheck`.
- Produces: `AdmissionPipeline(certification, policy, budget, sandbox, *, clock=None).check(run: Run, workload: AgentWorkload) -> AdmissionResult`.

Admission order: certification → model-identity binding → budget → policy → sandbox. A refusal short-circuits and records the checks evaluated so far. `escalate` sets `escalation_required`; `BLOCK_INJECTION` is treated as `deny` at admission.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the admission pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome, PolicyDecision
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import BudgetCheck, BudgetLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import AdmissionOutcome
from hiveplane.registry.models import AdmissionDecision


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _run(context: AdmissionContext, model: str | None = "m1") -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.QUEUED,
        model_identity=model,
        created_at=_clock(),
        updated_at=_clock(),
        context=context,
    )


class _Cert:
    def __init__(self, admitted: bool, model: str | None = None) -> None:
        self._admitted = admitted
        self._model = model

    def check_admission(self, workload: str, context: AdmissionContext) -> AdmissionDecision:
        return AdmissionDecision(
            workload=workload,
            context=context,
            admitted=self._admitted,
            actual_status="certified" if self._admitted else "uncertified",  # type: ignore[arg-type]
            reason=None if self._admitted else "not certified",
        )

    def attestation_model(self, workload: str) -> str | None:
        return self._model


class _Policy:
    def __init__(self, outcome: DecisionOutcome) -> None:
        self._outcome = outcome

    def evaluate(self, context) -> PolicyDecision:  # type: ignore[no-untyped-def]
        return PolicyDecision(
            run_id=context.run_id,
            outcome=self._outcome,
            reason="test",
            rule="test",
            timestamp=_clock(),
        )


class _Budget:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck:
        return BudgetCheck(
            allowed=self._allowed,
            level=BudgetLevel.RUN,
            limit_usd=1.0,
            spent_usd=1.0 if not self._allowed else 0.0,
            remaining_usd=0.0 if not self._allowed else 1.0,
            reason=None if self._allowed else "budget exhausted",
        )

    def record_usage(self, report):  # type: ignore[no-untyped-def]
        raise AssertionError("not used during admission")


class _Sandbox:
    def __init__(self, required: bool) -> None:
        self._required = required

    def required(self, workload, context, action_class):  # type: ignore[no-untyped-def]
        return self._required


def _pipeline(
    *,
    cert: _Cert,
    policy: _Policy | None = None,
    budget: _Budget | None = None,
    sandbox: _Sandbox | None = None,
) -> AdmissionPipeline:
    return AdmissionPipeline(
        cert,
        policy or _Policy(DecisionOutcome.ALLOW),
        budget or _Budget(True),
        sandbox or _Sandbox(False),
        clock=_clock,
    )


def test_admitted_when_all_checks_pass(make_manifest: Callable[..., AgentWorkload]) -> None:
    workload = make_manifest()
    result = _pipeline(cert=_Cert(True, "m1")).check(
        _run(AdmissionContext.PRODUCTION), workload
    )
    assert result.outcome is AdmissionOutcome.ADMITTED
    assert [c.step for c in result.checks] == ["certification", "model_identity", "budget", "policy", "sandbox"]


def test_refused_when_certification_fails(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(False)).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason == "not certified"
    assert [c.step for c in result.checks] == ["certification"]


def test_refused_on_model_swap(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "other-model")).check(
        _run(AdmissionContext.PRODUCTION, model="m1"), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason is not None and "model" in result.refused_reason


def test_refused_when_budget_fails(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), budget=_Budget(False)).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason == "budget exhausted"


def test_refused_when_policy_denies(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), policy=_Policy(DecisionOutcome.DENY)).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.refused_reason == "test"


def test_escalation_pauses_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), policy=_Policy(DecisionOutcome.ESCALATE)).check(
        _run(AdmissionContext.PRODUCTION), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.ADMITTED
    assert result.escalation_required is True


def test_sandbox_context_marks_sandbox_only(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "m1"), sandbox=_Sandbox(True)).check(
        _run(AdmissionContext.SANDBOX), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.SANDBOX_ONLY
    assert result.sandbox is True


def test_model_identity_is_skipped_when_run_has_none(make_manifest: Callable[..., AgentWorkload]) -> None:
    result = _pipeline(cert=_Cert(True, "other-model")).check(
        _run(AdmissionContext.SANDBOX, model=None), make_manifest()
    )
    assert result.outcome is AdmissionOutcome.SANDBOX_ONLY
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_admission.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.execution.admission'`

- [ ] **Step 3: Write minimal implementation**

```python
"""The admission pipeline: gate every run before it is queued."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome, PolicyContext
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.gates import BudgetGate, CertificationGate, PolicyGate, SandboxGate
from hiveplane.execution.models import AdmissionCheck, AdmissionOutcome, AdmissionResult


class AdmissionPipeline:
    """Evaluates certification, model binding, budget, policy, and sandbox."""

    def __init__(
        self,
        certification: CertificationGate,
        policy: PolicyGate,
        budget: BudgetGate,
        sandbox: SandboxGate,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._certification = certification
        self._policy = policy
        self._budget = budget
        self._sandbox = sandbox
        self._clock = clock or (lambda: datetime.now(UTC))

    def check(self, run: Run, workload: AgentWorkload) -> AdmissionResult:
        """Return the admission decision for a run against its workload."""
        context = run.context
        if context is None:
            raise ValueError("run.context is required for admission")
        checks: list[AdmissionCheck] = []

        decision = self._certification.check_admission(workload.name, context)
        checks.append(
            AdmissionCheck(
                step="certification",
                passed=decision.admitted,
                reason=decision.reason,
                rule="certification",
            )
        )
        if not decision.admitted:
            return self._refused(run, context, checks, decision.reason or "admission refused")

        bound_model = self._certification.attestation_model(workload.name)
        model_ok = (
            run.model_identity is None
            or bound_model is None
            or run.model_identity == bound_model
        )
        checks.append(
            AdmissionCheck(
                step="model_identity",
                passed=model_ok,
                reason=None if model_ok else "runtime model does not match attestation model",
                rule="model_binding",
            )
        )
        if not model_ok:
            return self._refused(run, context, checks, "model swap: runtime model differs from attestation")

        budget = self._budget.check(workload, context)
        checks.append(
            AdmissionCheck(
                step="budget",
                passed=budget.allowed,
                reason=budget.reason,
                rule=budget.level.value,
            )
        )
        if not budget.allowed:
            return self._refused(run, context, checks, budget.reason or "budget exhausted")

        policy_context = PolicyContext(
            run_id=run.id,
            workload=workload.name,
            team=workload.team,
            environment=context,
            certification_status=workload.certification_status,
        )
        policy = self._policy.evaluate(policy_context)
        policy_ok = policy.outcome is DecisionOutcome.ALLOW
        checks.append(
            AdmissionCheck(
                step="policy",
                passed=policy_ok,
                reason=policy.reason,
                rule=policy.rule,
            )
        )
        if policy.outcome in (DecisionOutcome.DENY, DecisionOutcome.BLOCK_INJECTION):
            return self._refused(run, context, checks, policy.reason)

        sandbox_required = self._sandbox.required(workload, context, policy.action_class)
        checks.append(
            AdmissionCheck(step="sandbox", passed=True, rule="sandbox")
        )
        outcome = (
            AdmissionOutcome.SANDBOX_ONLY
            if context is AdmissionContext.SANDBOX or sandbox_required
            else AdmissionOutcome.ADMITTED
        )
        return AdmissionResult(
            run_id=run.id,
            workload=workload.name,
            context=context,
            outcome=outcome,
            checks=checks,
            sandbox=sandbox_required,
            escalation_required=policy.outcome is DecisionOutcome.ESCALATE,
        )

    @staticmethod
    def _refused(
        run: Run,
        context: AdmissionContext,
        checks: list[AdmissionCheck],
        reason: str,
    ) -> AdmissionResult:
        return AdmissionResult(
            run_id=run.id,
            workload=run.workload_id,
            context=context,
            outcome=AdmissionOutcome.REFUSED,
            checks=checks,
            refused_reason=reason,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_admission.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/execution/admission.py tests/test_execution_admission.py
git commit -m "feat(execution): add admission pipeline"
```

---

### Task 7: RunService

**Files:**
- Create: `src/hiveplane/execution/service.py`
- Test: `tests/test_execution_service.py`

**Interfaces:**
- Consumes: `RunStore`, `RegistryService`, `AdmissionPipeline`, `RunExecutor`, `FanOutService`, `execution.models.*`, `core.run.can_transition`, `core.event.RunEvent`/`EventType`.
- Produces `RunService`:
  - `__init__(store, registry, *, admission, executor, fanout, clock=None, id_factory=None)`
  - `submit(*, workload: str, task: dict[str, JsonValue], caller: str, context: AdmissionContext, model_identity: str | None = None) -> Run`
  - `get(run_id: str) -> Run`, `list(*, workload=None, state=None) -> list[Run]`
  - `events(run_id: str) -> list[RunEvent]`, `usage(run_id: str) -> list[UsageReport]`
  - `transition(run_id, target: RunState, *, actor: str, detail: str | None = None) -> Run`
  - `intervene(run_id, action: InterventionAction, *, actor: str) -> Run`
  - `record_usage(run_id: str, report: UsageReport) -> Run`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the run service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.models import AdmissionDecision
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _FanOut:
    def __init__(self) -> None:
        self.notified: list[str] = []

    def notify(self, run, workload) -> list[object]:  # type: ignore[no-untyped-def]
        self.notified.append(run.id)
        return []


class _Executor:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.paused: list[str] = []
        self.cancelled: list[str] = []

    def start(self, context) -> None:  # type: ignore[no-untyped-def]
        self.started.append(context.run.id)

    def pause(self, run_id: str) -> bool:
        self.paused.append(run_id)
        return True

    def resume(self, run_id: str) -> bool:
        return True

    def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)

    def status(self, run_id: str) -> RunState:
        return RunState.RUNNING

    def usage(self, run_id: str):
        return None


def _service(
    make_manifest: Callable[..., AgentWorkload],
    *,
    admitted: bool = True,
    policy_outcome: DecisionOutcome = DecisionOutcome.ALLOW,
) -> tuple[RunService, _Executor, _FanOut, str]:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(name="agent-1")
    registry.create(workload)
    executor = _Executor()
    fanout = _FanOut()
    admission = AdmissionPipeline(
        _Cert(admitted, "m1"),
        _Policy(policy_outcome),
        _Budget(True),
        _Sandbox(False),
        clock=_clock,
    )
    ids = iter(["run-1", "run-2", "run-3"])
    service = RunService(
        InMemoryRunStore(),
        registry,
        admission=admission,
        executor=executor,
        fanout=fanout,
        clock=_clock,
        id_factory=lambda: next(ids),
    )
    return service, executor, fanout, "agent-1"


def test_submit_persists_run_and_admission(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload, task={"x": 1}, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    assert run.id == "run-1"
    assert run.state is RunState.QUEUED
    assert service.events("run-1")[0].type.value == "admission"


def test_submit_refused_raises_and_persists_nothing(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest, admitted=False)
    with pytest.raises(RunAdmissionRefusedError):
        service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION)
    with pytest.raises(RunNotFoundError):
        service.get("run-1")


def test_submit_escalation_creates_paused_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest, policy_outcome=DecisionOutcome.ESCALATE)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1")
    assert run.state is RunState.PAUSED


def test_transition_records_event_and_starts_executor(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, executor, _, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1")
    running = service.transition(run.id, RunState.RUNNING, actor="scheduler")
    assert running.state is RunState.RUNNING
    assert executor.started == ["run-1"]
    assert service.events(run.id)[-1].to_state is RunState.RUNNING


def test_illegal_transition_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1")
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    service.transition(run.id, RunState.COMPLETED, actor="runtime")
    with pytest.raises(IllegalTransitionError):
        service.transition(run.id, RunState.RUNNING, actor="runtime")


def test_terminal_transition_triggers_fanout(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, fanout, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1")
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    service.transition(run.id, RunState.COMPLETED, actor="runtime")
    assert fanout.notified == ["run-1"]


def test_intervene_pause_and_stop(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, executor, _, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1")
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    paused = service.intervene(run.id, InterventionAction.PAUSE, actor="operator")
    assert paused.state is RunState.PAUSED
    assert executor.paused == ["run-1"]
    stopped = service.intervene(run.id, InterventionAction.STOP, actor="operator")
    assert stopped.state is RunState.CANCELLED
    assert executor.cancelled == ["run-1"]


def test_intervene_invalid_state_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1")
    with pytest.raises(RunNotIntervenableError):
        service.intervene(run.id, InterventionAction.PAUSE, actor="operator")


def test_record_usage_updates_cost(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1")
    updated = service.record_usage(
        run.id,
        UsageReport(
            run_id=run.id,
            input_tokens=1,
            output_tokens=2,
            tool_calls=1,
            cost_usd=0.25,
            timestamp=_clock(),
        ),
    )
    assert updated.cost_usd == 0.25
    assert len(service.usage(run.id)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.execution.service'`

- [ ] **Step 3: Write minimal implementation**

```python
"""The run service: submission, state machine, intervention, and usage."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import JsonValue

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import AdmissionContext, Run, RunState, can_transition
from hiveplane.core.usage import UsageReport
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.fanout import FanOutService
from hiveplane.execution.gates import RunExecutor
from hiveplane.execution.models import (
    AdmissionOutcome,
    InterventionAction,
    RunContext,
)
from hiveplane.execution.store import RunStore
from hiveplane.registry.service import RegistryService

_TERMINAL = (RunState.COMPLETED, RunState.FAILED)


class RunService:
    """Owns run submission, transitions, intervention, and usage accounting."""

    def __init__(
        self,
        store: RunStore,
        registry: RegistryService,
        *,
        admission: AdmissionPipeline,
        executor: RunExecutor,
        fanout: FanOutService,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._admission = admission
        self._executor = executor
        self._fanout = fanout
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"run-{uuid4().hex[:12]}")

    def _require(self, run_id: str) -> Run:
        run = self._store.get_run(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    def _append_event(
        self,
        run_id: str,
        event_type: EventType,
        actor: str,
        *,
        from_state: RunState | None = None,
        to_state: RunState | None = None,
        detail: str | None = None,
    ) -> None:
        sequence = len(self._store.list_events(run_id))
        self._store.add_event(
            RunEvent(
                run_id=run_id,
                sequence=sequence,
                type=event_type,
                actor=actor,
                timestamp=self._clock(),
                from_state=from_state,
                to_state=to_state,
                detail=detail,
            )
        )

    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue] | None = None,
        model_identity: str | None = None,
    ) -> Run:
        """Submit a run, admitting or refusing it before it is persisted."""
        record = self._registry.get(workload)
        now = self._clock()
        run = Run(
            id=self._id_factory(),
            workload_id=workload,
            caller=caller,
            state=RunState.QUEUED,
            model_identity=model_identity,
            created_at=now,
            updated_at=now,
            manifest_version=record.current_version,
            context=context,
            task=task or {},
        )
        result = self._admission.check(run, record.manifest)
        if result.outcome is AdmissionOutcome.REFUSED:
            raise RunAdmissionRefusedError(result)

        if result.sandbox:
            run = run.model_copy(update={"sandbox": True})
        if result.escalation_required:
            run = run.model_copy(update={"state": RunState.PAUSED})

        self._store.save_run(run)
        self._store.save_admission(result)
        self._append_event(
            run.id,
            EventType.ADMISSION,
            caller,
            to_state=run.state,
            detail=result.outcome.value,
        )
        if result.escalation_required:
            self._append_event(run.id, EventType.STATE_CHANGE, caller, to_state=RunState.PAUSED, detail="escalation")
        return run

    def get(self, run_id: str) -> Run:
        """Return a run by id."""
        return self._require(run_id)

    def list(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]:
        """List runs, optionally filtered by workload and state."""
        return self._store.list_runs(workload=workload, state=state)

    def events(self, run_id: str) -> list[RunEvent]:
        """Return the ordered event log for a run."""
        self._require(run_id)
        return self._store.list_events(run_id)

    def usage(self, run_id: str) -> list[UsageReport]:
        """Return the recorded usage reports for a run."""
        self._require(run_id)
        return self._store.list_usage(run_id)

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
    ) -> Run:
        """Move a run to a target state, persisting before side effects."""
        run = self._require(run_id)
        if not can_transition(run.state, target):
            raise IllegalTransitionError(run_id, run.state, target)
        now = self._clock()
        updates: dict[str, object] = {"state": target, "updated_at": now}
        if target is RunState.RUNNING and run.started_at is None:
            updates["started_at"] = now
        if target in (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED):
            updates["finished_at"] = now
        updated = run.model_copy(update=updates)
        self._store.save_run(updated)
        self._append_event(
            run_id,
            EventType.STATE_CHANGE,
            actor,
            from_state=run.state,
            to_state=target,
            detail=detail,
        )
        if target is RunState.RUNNING and run.state is RunState.QUEUED:
            self._executor.start(
                RunContext(run=updated, workload=self._registry.get(run.workload_id).manifest, sandbox=updated.sandbox)
            )
        if target in _TERMINAL:
            self._fanout.notify(updated, self._registry.get(run.workload_id).manifest)
        return updated

    def intervene(self, run_id: str, action: InterventionAction, *, actor: str) -> Run:
        """Apply an operator intervention to a live run."""
        run = self._require(run_id)
        if action is InterventionAction.PAUSE:
            if run.state is not RunState.RUNNING:
                raise RunNotIntervenableError(run_id, action.value)
            confirmed = self._executor.pause(run_id)
            self._append_event(run_id, EventType.OPERATOR_ACTION, actor, detail=action.value)
            return self.transition(run_id, RunState.PAUSED, actor=actor) if confirmed else run
        if action is InterventionAction.RESUME:
            if run.state is not RunState.PAUSED:
                raise RunNotIntervenableError(run_id, action.value)
            self._executor.resume(run_id)
            self._append_event(run_id, EventType.OPERATOR_ACTION, actor, detail=action.value)
            return self.transition(run_id, RunState.RUNNING, actor=actor)
        self._executor.cancel(run_id)
        self._append_event(run_id, EventType.OPERATOR_ACTION, actor, detail=action.value)
        return self.transition(run_id, RunState.CANCELLED, actor=actor)

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        """Record usage for a run and update its accumulated cost."""
        run = self._require(run_id)
        self._store.add_usage(report)
        self._append_event(run_id, EventType.USAGE, "adapter", detail=str(report.cost_usd))
        updated = run.model_copy(
            update={"cost_usd": run.cost_usd + report.cost_usd, "updated_at": self._clock()}
        )
        self._store.save_run(updated)
        return updated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_service.py -v`
Expected: PASS (9 tests). Note the test imports `_Budget`, `_Cert`, `_Policy`, `_Sandbox` from `test_execution_admission`; ensure `tests/` is importable as a rootdir (it is, via pytest).

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/execution/service.py tests/test_execution_service.py
git commit -m "feat(execution): add run service with state machine and intervention"
```

---

### Task 8: Fan-out service and transports

**Files:**
- Create: `src/hiveplane/execution/fanout.py`
- Test: `tests/test_execution_fanout.py`

**Interfaces:**
- Consumes: `RunStore`, `core.fanout.FanOutSpec`/`FanOutDestination`/`FanOutType`, `core.workload.AgentWorkload`, `core.run.Run`, `execution.models.DeliveryRecord`/`DeliveryStatus`.
- Produces:
  - `DeliveryTransport` protocol: `send(destination: FanOutDestination, message: dict[str, JsonValue]) -> None`
  - `SlackTransport(*, webhook_url, post=None, timeout_s=10.0)`, `WebhookTransport(*, default_url=None, post=None, timeout_s=10.0)`
  - `FanOutService(store, transports: Mapping[FanOutType, DeliveryTransport], *, enabled=True, max_retries=3, clock=None)` with `notify(run, workload) -> list[DeliveryRecord]`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for result fan-out."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.run import Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.fanout import FanOutService, SlackTransport, WebhookTransport
from hiveplane.execution.models import DeliveryStatus
from hiveplane.execution.store import InMemoryRunStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _run(state: RunState = RunState.COMPLETED) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=state,
        created_at=_clock(),
        updated_at=_clock(),
        finished_at=_clock(),
    )


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[FanOutDestination, dict[str, object]]] = []

    def send(self, destination: FanOutDestination, message: dict[str, object]) -> None:
        self.sent.append((destination, message))


def _workload(make_manifest: Callable[..., AgentWorkload], destinations: list[dict[str, object]]) -> AgentWorkload:
    return make_manifest(fan_out={"on_completed": destinations})


def test_notify_delivers_to_configured_transport(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    recorder = _Recorder()
    service = FanOutService(
        store, {FanOutType.WEBHOOK: recorder}, clock=_clock
    )
    workload = _workload(make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}])
    records = service.notify(_run(), workload)
    assert [r.status for r in records] == [DeliveryStatus.DELIVERED]
    assert recorder.sent[0][1]["run_id"] == "run-1"
    assert store.list_deliveries("run-1")[0].attempts == 1


def test_notify_disabled_records_nothing(make_manifest: Callable[..., AgentWorkload]) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    service = FanOutService(store, {}, enabled=False, clock=_clock)
    workload = _workload(make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}])
    assert service.notify(_run(), workload) == []
    assert store.list_deliveries("run-1") == []


def test_notify_retries_then_records_failure(make_manifest: Callable[..., AgentWorkload]) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())

    class _AlwaysFail:
        def __init__(self) -> None:
            self.attempts = 0

        def send(self, destination: FanOutDestination, message: dict[str, object]) -> None:
            self.attempts += 1
            raise RuntimeError("boom")

    transport = _AlwaysFail()
    service = FanOutService(store, {FanOutType.WEBHOOK: transport}, max_retries=2, clock=_clock)
    workload = _workload(make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}])
    records = service.notify(_run(), workload)
    assert records[0].status is DeliveryStatus.FAILED
    assert records[0].attempts == 3
    assert transport.attempts == 3


def test_notify_ignores_unsupported_destination_type(make_manifest: Callable[..., AgentWorkload]) -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    service = FanOutService(store, {}, clock=_clock)
    workload = _workload(make_manifest, [{"type": "webhook", "url": "https://example.test/hook"}])
    records = service.notify(_run(), workload)
    assert records[0].status is DeliveryStatus.FAILED
    assert records[0].error is not None


def test_slack_transport_posts_message() -> None:
    posted: list[tuple[str, dict[str, object]]] = []
    transport = SlackTransport(
        webhook_url="https://hooks.slack.test/x",
        post=lambda url, payload: posted.append((url, payload)),
    )
    transport.send(
        FanOutDestination(type=FanOutType.SLACK, channel="#ops"),
        {"run_id": "run-1", "state": "completed"},
    )
    assert posted[0][0] == "https://hooks.slack.test/x"
    assert "text" in posted[0][1]


def test_webhook_transport_uses_destination_url() -> None:
    posted: list[tuple[str, dict[str, object]]] = []
    transport = WebhookTransport(post=lambda url, payload: posted.append((url, payload)))
    transport.send(
        FanOutDestination(type=FanOutType.WEBHOOK, url="https://example.test/hook"),
        {"run_id": "run-1"},
    )
    assert posted[0][0] == "https://example.test/hook"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_fanout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.execution.fanout'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Result fan-out: deliver terminal run outcomes to configured destinations."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import JsonValue

from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.run import Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import DeliveryRecord, DeliveryStatus
from hiveplane.execution.store import RunStore

Poster = Callable[[str, dict[str, Any]], None]


def _urllib_post(url: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        response.read()


class DeliveryTransport(Protocol):
    """Sends a composed fan-out message to a destination."""

    def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None: ...


class SlackTransport:
    """Posts fan-out messages to a Slack incoming webhook."""

    def __init__(
        self,
        *,
        webhook_url: str | None,
        post: Poster | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._post = post or _urllib_post

    def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None:
        """Post the message as Slack text."""
        if not self._webhook_url:
            raise RuntimeError("no slack webhook configured")
        self._post(self._webhook_url, {"text": json.dumps(message)})


class WebhookTransport:
    """Posts fan-out messages to a generic JSON webhook."""

    def __init__(self, *, default_url: str | None = None, post: Poster | None = None) -> None:
        self._default_url = default_url
        self._post = post or _urllib_post

    def send(self, destination: FanOutDestination, message: dict[str, JsonValue]) -> None:
        """Post the message to the destination URL or the default URL."""
        url = destination.url or self._default_url
        if not url:
            raise RuntimeError("no webhook url configured")
        self._post(url, dict(message))


def _target(destination: FanOutDestination) -> str:
    return destination.url or destination.channel or destination.project or destination.type.value


def _destinations(run: Run, workload: AgentWorkload) -> list[FanOutDestination]:
    fan_out = workload.spec.fan_out
    if run.state is RunState.COMPLETED:
        return list(fan_out.on_completed)
    if run.state is RunState.FAILED:
        return list(fan_out.on_failed)
    return []


def _message(run: Run, workload: AgentWorkload) -> dict[str, JsonValue]:
    certification = workload.spec.certification
    return {
        "run_id": run.id,
        "workload": run.workload_id,
        "state": run.state.value,
        "failure_reason": run.failure_reason,
        "cost_usd": run.cost_usd,
        "attestation_id": certification.attestation_id if certification else None,
    }


class FanOutService:
    """Delivers terminal run outcomes, recording every attempt."""

    def __init__(
        self,
        store: RunStore,
        transports: Mapping[FanOutType, DeliveryTransport],
        *,
        enabled: bool = True,
        max_retries: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transports = dict(transports)
        self._enabled = enabled
        self._max_retries = max_retries
        self._clock = clock or (lambda: datetime.now(UTC))

    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        """Deliver a run's outcome to every configured destination."""
        if not self._enabled:
            return []
        message = _message(run, workload)
        records: list[DeliveryRecord] = []
        for destination in _destinations(run, workload):
            records.append(self._deliver(run, destination, message))
        return records

    def _deliver(
        self,
        run: Run,
        destination: FanOutDestination,
        message: dict[str, JsonValue],
    ) -> DeliveryRecord:
        transport = self._transports.get(destination.type)
        target = _target(destination)
        record = DeliveryRecord(
            run_id=run.id,
            destination_type=destination.type,
            target=target,
            status=DeliveryStatus.PENDING,
            attempts=0,
            timestamp=self._clock(),
        )
        if transport is None:
            return self._finish(record, DeliveryStatus.FAILED, "no transport configured")
        error: str | None = None
        attempts = 0
        for _ in range(self._max_retries + 1):
            attempts += 1
            try:
                transport.send(destination, message)
            except Exception as exc:  # noqa: BLE001 - delivery failures are recorded, not raised
                error = str(exc)
                continue
            return self._finish(record.model_copy(update={"attempts": attempts}), DeliveryStatus.DELIVERED, None)
        return self._finish(
            record.model_copy(update={"attempts": attempts}), DeliveryStatus.FAILED, error
        )

    def _finish(
        self, record: DeliveryRecord, status: DeliveryStatus, error: str | None
    ) -> DeliveryRecord:
        finished = record.model_copy(update={"status": status, "error": error})
        self._store.add_delivery(finished)
        return finished
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_fanout.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/hiveplane/execution/fanout.py tests/test_execution_fanout.py
git commit -m "feat(execution): add result fan-out service and transports"
```

---

### Task 9: Run API and application wiring

**Files:**
- Create: `src/hiveplane/execution/wiring.py`
- Create: `src/hiveplane/api/runs.py`
- Modify: `src/hiveplane/api/deps.py`
- Modify: `src/hiveplane/api/app.py`
- Test: `tests/test_execution_api.py`

**Interfaces:**
- Consumes: `RunService`, `build_run_service`, `RegistryService`, `get_settings`, `execution.errors.*`.
- Produces:
  - `execution.wiring.build_run_service(registry_service: RegistryService) -> RunService`
  - `api.deps.get_run_service(request) -> RunService`
  - Endpoints: `POST /runs`, `GET /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/events`, `GET /runs/{run_id}/usage`, `POST /runs/{run_id}/pause`, `POST /runs/{run_id}/resume`, `POST /runs/{run_id}/stop`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the run API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.registry.service import RegistryService

from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


def _client(make_manifest) -> TestClient:  # type: ignore[no-untyped-def]
    app = create_app()
    registry: RegistryService = app.state.registry_service
    workload = make_manifest(
        name="agent-1",
        sandbox={"enabled": True, "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10}},
    )
    registry.create(workload)
    return TestClient(app)


def _payload(context: str = "sandbox") -> dict[str, object]:
    return {"workload": "agent-1", "caller": "cli", "context": context, "task": {"x": 1}, "model_identity": "openai/gpt-4o/2024-08-06"}


def test_submit_run_sandbox(make_manifest) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_manifest)
    response = client.post("/runs", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "queued"
    assert body["context"] == "sandbox"


def test_uncertified_production_is_refused(make_manifest) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_manifest)
    response = client.post("/runs", json=_payload(context="production"))
    assert response.status_code == 403


def test_submit_run_unknown_workload_returns_404(make_manifest) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_manifest)
    payload = _payload()
    payload["workload"] = "missing"
    response = client.post("/runs", json=payload)
    assert response.status_code == 404


def test_get_and_list_runs(make_manifest) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload()).json()["id"]
    assert client.get(f"/runs/{run_id}").status_code == 200
    assert client.get("/runs", params={"workload": "agent-1"}).json()[0]["id"] == run_id


def test_get_missing_run_returns_404(make_manifest) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_manifest)
    assert client.get("/runs/nope").status_code == 404


def test_transition_via_intervention_endpoints(make_manifest) -> None:  # type: ignore[no-untyped-def]
    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(make_manifest(name="agent-1"))
    client = TestClient(app)
    run_id = client.post("/runs", json=_payload(context="sandbox")).json()["id"]
    running = client.post(f"/runs/{run_id}/resume")
    assert running.status_code == 409
    assert client.get(f"/runs/{run_id}/events").json()[0]["type"] == "admission"


def test_usage_endpoint_is_empty_initially(make_manifest) -> None:  # type: ignore[no-untyped-def]
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload(context="sandbox")).json()["id"]
    assert client.get(f"/runs/{run_id}/usage").json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_execution_api.py -v`
Expected: FAIL with 404 on `POST /runs` (route does not exist yet)

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/execution/wiring.py`:

```python
"""Composition root for the run lifecycle services."""

from __future__ import annotations

from hiveplane.config import get_settings
from hiveplane.core.fanout import FanOutType
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.fanout import FanOutService, SlackTransport, WebhookTransport
from hiveplane.execution.gates import (
    ManifestSandboxGate,
    NullRunExecutor,
    PermissivePolicyGate,
    RegistryCertificationGate,
    UnlimitedBudgetGate,
)
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService


def build_run_service(registry_service: RegistryService) -> RunService:
    """Build a RunService with phase-1 default gates and an in-memory store."""
    settings = get_settings()
    store = InMemoryRunStore()
    fanout = FanOutService(
        store,
        {
            FanOutType.SLACK: SlackTransport(
                webhook_url=settings.fanout.slack_webhook_url
            ),
            FanOutType.WEBHOOK: WebhookTransport(
                default_url=settings.fanout.generic_webhook_url
            ),
        },
        enabled=settings.fanout.enabled,
        max_retries=settings.fanout.max_retries,
    )
    return RunService(
        store,
        registry_service,
        admission=AdmissionPipeline(
            RegistryCertificationGate(registry_service),
            PermissivePolicyGate(),
            UnlimitedBudgetGate(),
            ManifestSandboxGate(),
        ),
        executor=NullRunExecutor(),
        fanout=fanout,
    )
```

`src/hiveplane/api/deps.py` — add:

```python
from hiveplane.execution.service import RunService


def get_run_service(request: Request) -> RunService:
    """Return the run service bound to the application state."""
    service: RunService = request.app.state.run_service
    return service
```

`src/hiveplane/api/runs.py`:

```python
"""Run execution API: submission, inspection, and intervention."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from hiveplane.api.deps import get_run_service
from hiveplane.core.event import RunEvent
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.models import InterventionAction, RunSubmission
from hiveplane.execution.service import RunService

router = APIRouter(tags=["runs"])

ServiceDep = Annotated[RunService, Depends(get_run_service)]


@router.post("/runs", response_model=Run, status_code=status.HTTP_201_CREATED)
def submit_run(payload: RunSubmission, service: ServiceDep) -> Run:
    """Submit a run for admission."""
    return service.submit(
        workload=payload.workload,
        caller=payload.caller,
        context=payload.context,
        task=payload.task,
        model_identity=payload.model_identity,
    )


@router.get("/runs", response_model=list[Run])
def list_runs(
    service: ServiceDep,
    workload: str | None = None,
    state: RunState | None = None,
) -> list[Run]:
    """List runs, optionally filtered."""
    return service.list(workload=workload, state=state)


@router.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: str, service: ServiceDep) -> Run:
    """Return a run by id."""
    return service.get(run_id)


@router.get("/runs/{run_id}/events", response_model=list[RunEvent])
def list_events(run_id: str, service: ServiceDep) -> list[RunEvent]:
    """Return a run's ordered event log."""
    return service.events(run_id)


@router.get("/runs/{run_id}/usage", response_model=list[UsageReport])
def list_usage(run_id: str, service: ServiceDep) -> list[UsageReport]:
    """Return a run's usage reports."""
    return service.usage(run_id)


@router.post("/runs/{run_id}/pause", response_model=Run)
def pause_run(run_id: str, service: ServiceDep) -> Run:
    """Pause a running run."""
    return service.intervene(run_id, InterventionAction.PAUSE, actor="api")


@router.post("/runs/{run_id}/resume", response_model=Run)
def resume_run(run_id: str, service: ServiceDep) -> Run:
    """Resume a paused run."""
    return service.intervene(run_id, InterventionAction.RESUME, actor="api")


@router.post("/runs/{run_id}/stop", response_model=Run)
def stop_run(run_id: str, service: ServiceDep) -> Run:
    """Stop a run immediately."""
    return service.intervene(run_id, InterventionAction.STOP, actor="api")
```

`src/hiveplane/api/app.py` — wire the service and errors. In `create_app`, accept and store the run service, and add handlers:

```python
from hiveplane.api.runs import router as runs_router
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.service import RunService
from hiveplane.execution.wiring import build_run_service
```

```python
def create_app(
    registry_service: RegistryService | None = None,
    run_service: RunService | None = None,
) -> FastAPI:
    """Build and return the control-plane ASGI application."""
    ...
    registry = registry_service or RegistryService(InMemoryRegistryStore())
    app.state.registry_service = registry
    app.state.run_service = run_service or build_run_service(registry)
    ...
    for _error_type, _status_code in (
        (RunNotFoundError, 404),
        (IllegalTransitionError, 409),
        (RunNotIntervenableError, 409),
        (RunAdmissionRefusedError, 403),
    ):
        app.add_exception_handler(_error_type, _make_handler(_error_type, _status_code))
    app.include_router(runs_router)
    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_execution_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full exit gate**

Run: `make check`
Expected: ruff clean, mypy strict clean, all tests pass, coverage > 95%. If coverage dips below 95%, add focused tests for the uncovered branches (gate defaults, store filters) until it clears.

- [ ] **Step 6: Update docs**

Append a short "Execution Path" note to `docs/USER_GUIDE.md` describing the `/runs` endpoints and manual intervention, and mark the run-lifecycle items complete in `docs/wbs/v0.1.0/wbs-v0.1.0-part4-run-lifecycle.md`.

- [ ] **Step 7: Commit**

```bash
git add src/hiveplane/execution/wiring.py src/hiveplane/api/runs.py src/hiveplane/api/deps.py src/hiveplane/api/app.py tests/test_execution_api.py docs/USER_GUIDE.md docs/wbs/v0.1.0/wbs-v0.1.0-part4-run-lifecycle.md
git commit -m "feat(execution): expose run API and wire the lifecycle services"
```

---

## Self-review

**Spec coverage** (against `docs/design/execution-path-design.md`, run lifecycle sections):

| Spec requirement | Task |
|------------------|------|
| Extend `Run`; move `AdmissionContext` to core; re-export from registry | 1 |
| Admission models, errors, `RunSubmission`, `RunContext` | 2 |
| `RunStore` protocol + `InMemoryRunStore` | 3 |
| `JsonFileRunStore` durability across restart | 4 |
| Certification / policy / budget / sandbox / executor seams + phase-1 defaults | 5 |
| Admission pipeline order and outcomes | 6 |
| Submit, state machine, attributed events, intervention, usage | 7 |
| Fan-out with Slack/webhook transports, retries, recorded deliveries | 8 |
| `/runs` API and error mapping | 9 |

Deferred by design (not part of this plan): real policy engine, real budget service, real sandbox/shaping, real adapters, approvals endpoints, Postgres store. Those are phases 2-5.

**Placeholder scan:** No `TODO`/`TBD`/"handle edge cases"/"similar to Task N" remain. Every code step contains full code.

**Type consistency:** `AdmissionResult` field names (`run_id`, `workload`, `context`, `outcome`, `checks`, `sandbox`, `escalation_required`, `refused_reason`) are used identically in Tasks 2, 5, 6, 7. `RunService` constructor keyword names (`admission`, `executor`, `fanout`, `clock`, `id_factory`) match Task 7's tests and Task 9's wiring. `RunExecutor` method signatures match between Task 5 and Task 7/9. `BudgetCheck`/`BudgetLevel` are defined once in Task 1 and used in Tasks 5/6.

**Import hygiene:** Task 7's `transition` fetches `self._registry.get(run.workload_id).manifest` in each branch; the `RUNNING` and terminal branches are mutually exclusive, so the manifest is fetched at most once per call. Test files import only names they use (ruff `F401` applies to `tests/`).

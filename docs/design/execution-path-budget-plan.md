# Budget Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the phase-1 unlimited budget gate with a real cost table and per-run/per-day/per-team enforcement that blocks over-budget runs and emits burn metrics.

**Architecture:** `budget/` owns the `CostTable` (per-model token pricing) and `BudgetService` (implements the `BudgetGate` seam). The budget seam gains a `BudgetOutcome` return (check plus computed cost) and takes the workload so day/team attribution works. `RunService` records usage through the budget service and fails a run when a limit is exceeded.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, pytest, ruff, mypy strict.

## Global Constraints

- Python `>=3.12`; pydantic v2; no new runtime dependency.
- Every task leaves `make check` green: ruff, mypy strict, `pytest --cov` total > 95%.
- Ruff line length 100; `ANN` enforced in `src/`, ignored in `tests/`. Mypy strict in `src/`.
- No code comments; docstrings in repo style. No date/milestone-named files.
- Injected `clock`; timezone-aware datetimes.
- Known token counts map to expected cost; unknown model identities fail loudly.
- Reuse: `core.usage.UsageReport`/`BudgetCheck`/`BudgetLevel`, `execution.gates.BudgetGate`, `execution.service.RunService`, `execution.wiring.build_run_service`.

---

## Phase scope

Phase 3 of the execution path (see `docs/design/execution-path-design.md`):

1. Run lifecycle core — complete
2. Policy engine and approvals — complete
3. **Budget ← this plan**
4. Sandbox and shaping
5. Adapters and conformance

Cost showback analytics (cost-per-completed-task, waste, ROI flags) are the cost-service effort and out of scope; this plan records per-event `CostAttribution` and emits burn metrics.

---

## File structure

| File | Responsibility |
|------|----------------|
| `src/hiveplane/core/usage.py` | Add `UsageReport.model_identity`, add `BudgetOutcome` |
| `src/hiveplane/execution/gates.py` | `BudgetGate.record_usage(workload, report) -> BudgetOutcome`; update `UnlimitedBudgetGate` |
| `src/hiveplane/execution/service.py` | Budget enforcement in `record_usage`; `budget` constructor seam |
| `src/hiveplane/execution/wiring.py` | Build and wire the budget service |
| `src/hiveplane/api/app.py` | Build budget service; app state |
| `src/hiveplane/budget/__init__.py` | Package marker |
| `src/hiveplane/budget/errors.py` | `BudgetError`, `UnknownModelPriceError` |
| `src/hiveplane/budget/pricing.py` | `ModelPrice`, `CostTable` |
| `src/hiveplane/budget/models.py` | `CostAttribution`, `BudgetSnapshot` |
| `src/hiveplane/budget/store.py` | `BudgetStore` + in-memory implementation |
| `src/hiveplane/budget/metrics.py` | `BudgetMetrics` protocol + `NullBudgetMetrics` |
| `src/hiveplane/budget/service.py` | `BudgetService` |
| `tests/test_budget_pricing.py` | Pricing tests |
| `tests/test_budget_store.py` | Store tests |
| `tests/test_budget_service.py` | Service tests |
| `tests/test_execution_budget.py` | RunService enforcement tests |
| `tests/test_execution_gates.py` | Update `UnlimitedBudgetGate` test |
| `tests/test_execution_admission.py` | Update `_Budget` fake |

---

### Task 1: Budget seam — outcome type and workload-aware record

**Files:**
- Modify: `src/hiveplane/core/usage.py`
- Modify: `src/hiveplane/execution/gates.py`
- Modify: `tests/test_execution_gates.py`
- Modify: `tests/test_execution_admission.py`
- Test: `tests/test_core_run_models.py` (extend)

**Interfaces:**
- Consumes: `core.usage.UsageReport`, `core.usage.BudgetCheck`.
- Produces:
  - `core.usage.UsageReport.model_identity: str | None`
  - `core.usage.BudgetOutcome(check: BudgetCheck, cost_usd: float)`
  - `execution.gates.BudgetGate` with `check(workload, context) -> BudgetCheck` and `record_usage(workload, report) -> BudgetOutcome`
  - `execution.gates.UnlimitedBudgetGate` updated to the new signature

- [ ] **Step 1: Write the failing test**

Append to `tests/test_core_run_models.py`:

```python
from hiveplane.core.usage import BudgetOutcome


def test_usage_report_carries_model_identity() -> None:
    report = UsageReport(
        run_id="run-1",
        input_tokens=10,
        output_tokens=5,
        tool_calls=1,
        cost_usd=0.02,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        model_identity="openai/gpt-4o/2024-08-06",
    )
    assert report.model_identity == "openai/gpt-4o/2024-08-06"


def test_budget_outcome_wraps_check_and_cost() -> None:
    check = BudgetCheck(
        allowed=True, level=BudgetLevel.RUN, limit_usd=1.0, spent_usd=0.02, remaining_usd=0.98
    )
    outcome = BudgetOutcome(check=check, cost_usd=0.02)
    assert outcome.check.allowed is True
    assert outcome.cost_usd == 0.02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_core_run_models.py -k "model_identity or budget_outcome" -v`
Expected: FAIL — `ImportError: cannot import name 'BudgetOutcome'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/core/usage.py` — add `model_identity` to `UsageReport` and the `BudgetOutcome` model:

```python
class UsageReport(BaseModel):
    """Token, tool-call, and cost usage reported for a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    timestamp: AwareDatetime
    model_identity: str | None = None

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens


class BudgetOutcome(BaseModel):
    """A usage record's effect on budget: the check and the priced cost."""

    model_config = ConfigDict(extra="forbid")

    check: BudgetCheck
    cost_usd: float = Field(ge=0.0)
```

`src/hiveplane/execution/gates.py` — import `BudgetOutcome` and change the protocol and default:

```python
from hiveplane.core.usage import BudgetCheck, BudgetLevel, BudgetOutcome, UsageReport
```

```python
class BudgetGate(Protocol):
    """Checks budget headroom and records priced usage."""

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck: ...

    def record_usage(self, workload: AgentWorkload, report: UsageReport) -> BudgetOutcome: ...


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

    def record_usage(self, workload: AgentWorkload, report: UsageReport) -> BudgetOutcome:
        """Return an allow outcome that passes cost through unchanged."""
        limit = workload.spec.budget.per_run_usd
        return BudgetOutcome(
            check=BudgetCheck(
                allowed=True,
                level=BudgetLevel.RUN,
                limit_usd=limit,
                spent_usd=report.cost_usd,
                remaining_usd=max(0.0, limit - report.cost_usd),
            ),
            cost_usd=report.cost_usd,
        )
```

`tests/test_execution_gates.py` — update the budget test:

```python
def test_unlimited_budget_gate_allows(make_manifest: Callable[..., AgentWorkload]) -> None:
    workload = make_manifest()
    gate = UnlimitedBudgetGate()
    check = gate.check(workload, AdmissionContext.PRODUCTION)
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
    assert gate.record_usage(workload, usage).check.allowed is True
```

`tests/test_execution_admission.py` — update the `_Budget` fake to the new seam (it must satisfy `BudgetGate`):

```python
class _Budget:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck:
        return BudgetCheck(
            allowed=self._allowed,
            level=BudgetLevel.RUN,
            limit_usd=1.0,
            spent_usd=0.0 if self._allowed else 1.0,
            remaining_usd=1.0 if self._allowed else 0.0,
            reason=None if self._allowed else "budget exhausted",
        )

    def record_usage(self, workload: AgentWorkload, report: UsageReport) -> BudgetOutcome:
        raise AssertionError("not used during admission")
```

Add `UsageReport, BudgetOutcome` to the `tests/test_execution_admission.py` imports from `hiveplane.core.usage`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_core_run_models.py tests/test_execution_gates.py tests/test_execution_admission.py -q`
Expected: PASS

- [ ] **Step 5: Full gate + commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m mypy src/ tests/
git add src/hiveplane/core/usage.py src/hiveplane/execution/gates.py tests/test_core_run_models.py tests/test_execution_gates.py tests/test_execution_admission.py
git commit -m "feat(budget): add budget outcome type and workload-aware usage recording"
git push origin main
```

---

### Task 2: Cost table and pricing

**Files:**
- Create: `src/hiveplane/budget/__init__.py`
- Create: `src/hiveplane/budget/errors.py`
- Create: `src/hiveplane/budget/pricing.py`
- Test: `tests/test_budget_pricing.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `budget.errors.BudgetError`, `budget.errors.UnknownModelPriceError`
  - `budget.pricing.ModelPrice(input_per_1k, output_per_1k)`
  - `budget.pricing.CostTable(prices=None)` with `price(model_identity, input_tokens, output_tokens) -> float`
  - `budget.pricing.DEFAULT_PRICES`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the model cost table."""

from __future__ import annotations

import pytest

from hiveplane.budget.errors import UnknownModelPriceError
from hiveplane.budget.pricing import DEFAULT_PRICES, CostTable, ModelPrice


def test_known_tokens_map_to_expected_cost() -> None:
    table = CostTable()
    cost = table.price("openai/gpt-4o/2024-08-06", input_tokens=1000, output_tokens=1000)
    assert cost == pytest.approx(0.005 + 0.015)


def test_unknown_model_fails_loudly() -> None:
    with pytest.raises(UnknownModelPriceError):
        CostTable().price("acme/mystery/1", input_tokens=1, output_tokens=1)


def test_custom_table_overrides_defaults() -> None:
    table = CostTable(prices={"acme/mystery/1": ModelPrice(input_per_1k=1.0, output_per_1k=2.0)})
    assert table.price("acme/mystery/1", 1000, 500) == pytest.approx(2.0)
    assert "openai/gpt-4o/2024-08-06" in DEFAULT_PRICES


def test_zero_tokens_cost_nothing() -> None:
    assert CostTable().price("openai/gpt-4o/2024-08-06", 0, 0) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_budget_pricing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.budget'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/budget/__init__.py`:

```python
"""Cost pricing and budget enforcement."""
```

`src/hiveplane/budget/errors.py`:

```python
"""Budget domain errors."""

from __future__ import annotations


class BudgetError(Exception):
    """Base class for budget errors."""


class UnknownModelPriceError(BudgetError):
    """Raised when a model identity has no price in the cost table."""

    def __init__(self, model_identity: str) -> None:
        super().__init__(f"no price configured for model {model_identity!r}")
        self.model_identity = model_identity
```

`src/hiveplane/budget/pricing.py`:

```python
"""Per-model token pricing (DD-04, D5)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.budget.errors import UnknownModelPriceError


class ModelPrice(BaseModel):
    """Token prices for a model, in USD per 1,000 tokens."""

    model_config = ConfigDict(extra="forbid")

    input_per_1k: float = Field(ge=0.0)
    output_per_1k: float = Field(ge=0.0)


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "openai/gpt-4o/2024-08-06": ModelPrice(input_per_1k=0.005, output_per_1k=0.015),
    "openai/gpt-4o-mini/2024-07-18": ModelPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    "anthropic/claude-3-5-sonnet/20241022": ModelPrice(
        input_per_1k=0.003, output_per_1k=0.015
    ),
}


class CostTable:
    """Maps exact model identities to prices and prices token usage."""

    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self._prices = dict(DEFAULT_PRICES)
        if prices is not None:
            self._prices.update(prices)

    def price(self, model_identity: str, input_tokens: int, output_tokens: int) -> float:
        """Return the USD cost of token usage, raising for unknown models."""
        price = self._prices.get(model_identity)
        if price is None:
            raise UnknownModelPriceError(model_identity)
        return (input_tokens / 1000.0) * price.input_per_1k + (
            output_tokens / 1000.0
        ) * price.output_per_1k
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_budget_pricing.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Full gate + commit**

```bash
git add src/hiveplane/budget tests/test_budget_pricing.py
git commit -m "feat(budget): add per-model cost table and pricing"
git push origin main
```

---

### Task 3: Budget store and attribution models

**Files:**
- Create: `src/hiveplane/budget/models.py`
- Create: `src/hiveplane/budget/store.py`
- Test: `tests/test_budget_store.py`

**Interfaces:**
- Consumes: `core.usage.BudgetLevel`.
- Produces:
  - `budget.models.CostAttribution` (run_id, workload, team, model_identity, input_tokens, output_tokens, tool_calls, cost_usd, timestamp)
  - `budget.models.BudgetSnapshot` (run_usd, day_usd, team_usd, day, workload)
  - `budget.store.BudgetStore` protocol + `InMemoryBudgetStore` with `add_run_spend`, `run_spend`, `add_day_spend`, `day_spend`, `add_team_spend`, `team_spend`, `record_attribution`, `list_attributions`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the budget store."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.budget.models import CostAttribution
from hiveplane.budget.store import InMemoryBudgetStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_run_day_team_spend_accumulates() -> None:
    store = InMemoryBudgetStore()
    store.add_run_spend("run-1", 0.25)
    store.add_run_spend("run-1", 0.25)
    store.add_day_spend("agent-1", "2026-01-01", 0.5)
    store.add_team_spend("platform", "2026-01-01", 0.5)
    assert store.run_spend("run-1") == 0.5
    assert store.day_spend("agent-1", "2026-01-01") == 0.5
    assert store.team_spend("platform", "2026-01-01") == 0.5
    assert store.run_spend("missing") == 0.0
    assert store.day_spend("agent-1", "2026-01-02") == 0.0
    assert store.team_spend("platform", "2026-01-02") == 0.0


def test_attributions_round_trip() -> None:
    store = InMemoryBudgetStore()
    store.record_attribution(
        CostAttribution(
            run_id="run-1",
            workload="agent-1",
            team="platform",
            model_identity="openai/gpt-4o/2024-08-06",
            input_tokens=100,
            output_tokens=50,
            tool_calls=1,
            cost_usd=0.02,
            timestamp=_clock(),
        )
    )
    records = store.list_attributions(workload="agent-1")
    assert len(records) == 1
    assert records[0].cost_usd == 0.02
    assert store.list_attributions(workload="other") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_budget_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.budget.store'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/budget/models.py`:

```python
"""Cost attribution and budget snapshot models (D5, D14)."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class CostAttribution(BaseModel):
    """A priced usage event attributed to a run, workload, and team."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    team: str | None = None
    model_identity: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    timestamp: AwareDatetime


class BudgetSnapshot(BaseModel):
    """Current spend for a workload and team over a day."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    team: str | None = None
    day: str
    run_usd: float = Field(ge=0.0)
    day_usd: float = Field(ge=0.0)
    team_usd: float = Field(ge=0.0)
```

`src/hiveplane/budget/store.py`:

```python
"""Budget spend and attribution storage."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.budget.models import CostAttribution


class BudgetStore(Protocol):
    """Storage for spend totals and attributions."""

    def add_run_spend(self, run_id: str, amount: float) -> None: ...

    def run_spend(self, run_id: str) -> float: ...

    def add_day_spend(self, workload: str, day: str, amount: float) -> None: ...

    def day_spend(self, workload: str, day: str) -> float: ...

    def add_team_spend(self, team: str, day: str, amount: float) -> None: ...

    def team_spend(self, team: str, day: str) -> float: ...

    def record_attribution(self, record: CostAttribution) -> None: ...

    def list_attributions(self, *, workload: str | None = None) -> list[CostAttribution]: ...


class InMemoryBudgetStore:
    """A process-local, thread-safe budget store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._run_spend: dict[str, float] = {}
        self._day_spend: dict[tuple[str, str], float] = {}
        self._team_spend: dict[tuple[str, str], float] = {}
        self._attributions: list[CostAttribution] = []

    def add_run_spend(self, run_id: str, amount: float) -> None:
        """Add spend to a run total."""
        with self._lock:
            self._run_spend[run_id] = self._run_spend.get(run_id, 0.0) + amount

    def run_spend(self, run_id: str) -> float:
        """Return a run's total spend."""
        with self._lock:
            return self._run_spend.get(run_id, 0.0)

    def add_day_spend(self, workload: str, day: str, amount: float) -> None:
        """Add spend to a workload's day total."""
        with self._lock:
            key = (workload, day)
            self._day_spend[key] = self._day_spend.get(key, 0.0) + amount

    def day_spend(self, workload: str, day: str) -> float:
        """Return a workload's day spend."""
        with self._lock:
            return self._day_spend.get((workload, day), 0.0)

    def add_team_spend(self, team: str, day: str, amount: float) -> None:
        """Add spend to a team's day total."""
        with self._lock:
            key = (team, day)
            self._team_spend[key] = self._team_spend.get(key, 0.0) + amount

    def team_spend(self, team: str, day: str) -> float:
        """Return a team's day spend."""
        with self._lock:
            return self._team_spend.get((team, day), 0.0)

    def record_attribution(self, record: CostAttribution) -> None:
        """Append a cost attribution record."""
        with self._lock:
            self._attributions.append(record.model_copy(deep=True))

    def list_attributions(self, *, workload: str | None = None) -> list[CostAttribution]:
        """List attribution records, optionally filtered by workload."""
        with self._lock:
            records = list(self._attributions)
        if workload is not None:
            records = [record for record in records if record.workload == workload]
        return [record.model_copy(deep=True) for record in records]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_budget_store.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Full gate + commit**

```bash
git add src/hiveplane/budget/models.py src/hiveplane/budget/store.py tests/test_budget_store.py
git commit -m "feat(budget): add cost attribution models and budget store"
git push origin main
```

---

### Task 4: Budget service and burn metrics

**Files:**
- Create: `src/hiveplane/budget/metrics.py`
- Create: `src/hiveplane/budget/service.py`
- Test: `tests/test_budget_service.py`

**Interfaces:**
- Consumes: `CostTable`, `BudgetStore`, `core.usage.BudgetCheck`/`BudgetLevel`/`BudgetOutcome`/`UsageReport`, `core.run.AdmissionContext`, `core.workload.AgentWorkload`, `budget.models.CostAttribution`/`BudgetSnapshot`.
- Produces:
  - `budget.metrics.BudgetMetrics` protocol (`record_spend`, `record_exceeded`) + `NullBudgetMetrics`
  - `budget.service.BudgetService(store, pricing, *, metrics=None, clock=None)` with `check(workload, context) -> BudgetCheck`, `record_usage(workload, report) -> BudgetOutcome`, `snapshot(workload, run_id) -> BudgetSnapshot`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the budget service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.budget.errors import UnknownModelPriceError
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import BudgetLevel, UsageReport
from hiveplane.core.workload import AgentWorkload


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _Metrics:
    def __init__(self) -> None:
        self.spends: list[float] = []
        self.exceeded: list[BudgetLevel] = []

    def record_spend(self, cost_usd: float, workload: str, team: str | None) -> None:
        self.spends.append(cost_usd)

    def record_exceeded(self, level: BudgetLevel, workload: str) -> None:
        self.exceeded.append(level)


def _service(*, per_run: float = 1.0) -> tuple[BudgetService, InMemoryBudgetStore, _Metrics]:
    store = InMemoryBudgetStore()
    metrics = _Metrics()
    service = BudgetService(store, CostTable(), metrics=metrics, clock=_clock)
    return service, store, metrics


def _report(cost: float = 0.0, tokens: int = 0) -> UsageReport:
    return UsageReport(
        run_id="run-1",
        input_tokens=tokens,
        output_tokens=0,
        tool_calls=1,
        cost_usd=cost,
        timestamp=_clock(),
        model_identity="openai/gpt-4o/2024-08-06",
    )


def test_check_allows_within_budget(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service()
    check = service.check(make_manifest(), AdmissionContext.PRODUCTION)
    assert check.allowed is True
    assert check.level is BudgetLevel.RUN


def test_check_denies_when_day_exhausted(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, store, _ = _service()
    workload = make_manifest()
    store.add_day_spend(workload.name, "2026-01-01", workload.spec.budget.per_day_usd)
    check = service.check(workload, AdmissionContext.PRODUCTION)
    assert check.allowed is False
    assert check.level is BudgetLevel.DAY


def test_record_usage_prices_tokens_and_attributes(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, store, metrics = _service()
    workload = make_manifest()
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.cost_usd == pytest.approx(0.005)
    assert outcome.check.allowed is True
    assert store.run_spend("run-1") == pytest.approx(0.005)
    assert store.list_attributions(workload="agent-1")[0].tool_calls == 1
    assert metrics.spends == [pytest.approx(0.005)]


def test_record_usage_blocks_run_over_per_run_budget(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, metrics = _service()
    workload = make_manifest(budget={"per_run_usd": 0.001, "per_day_usd": 5.0})
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.check.allowed is False
    assert outcome.check.level is BudgetLevel.RUN
    assert BudgetLevel.RUN in metrics.exceeded


def test_record_usage_blocks_run_over_day_budget(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, store, _ = _service()
    workload = make_manifest(budget={"per_run_usd": 1.0, "per_day_usd": 0.001})
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.check.allowed is False
    assert outcome.check.level is BudgetLevel.DAY


def test_unknown_model_fails_loudly(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service()
    report = UsageReport(
        run_id="run-1",
        input_tokens=1,
        output_tokens=1,
        tool_calls=0,
        cost_usd=0.0,
        timestamp=_clock(),
        model_identity="acme/mystery/1",
    )
    with pytest.raises(UnknownModelPriceError):
        service.record_usage(make_manifest(), report)


def test_snapshot_reports_spend(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service()
    workload = make_manifest()
    service.record_usage(workload, _report(tokens=1000))
    snapshot = service.snapshot(workload, "run-1")
    assert snapshot.run_usd == pytest.approx(0.005)
    assert snapshot.team_usd == pytest.approx(0.005)
    assert snapshot.day == "2026-01-01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_budget_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hiveplane.budget.service'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/budget/metrics.py`:

```python
"""Budget burn metrics hooks (real exporters land with telemetry)."""

from __future__ import annotations

from typing import Protocol

from hiveplane.core.usage import BudgetLevel


class BudgetMetrics(Protocol):
    """Sink for budget burn and over-budget signals."""

    def record_spend(self, cost_usd: float, workload: str, team: str | None) -> None: ...

    def record_exceeded(self, level: BudgetLevel, workload: str) -> None: ...


class NullBudgetMetrics:
    """A metrics sink that records nothing."""

    def record_spend(self, cost_usd: float, workload: str, team: str | None) -> None:
        """Discard a spend signal."""

    def record_exceeded(self, level: BudgetLevel, workload: str) -> None:
        """Discard an over-budget signal."""
```

`src/hiveplane/budget/service.py`:

```python
"""Cost pricing and per-run/per-day/per-team budget enforcement (DD-04)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.budget.metrics import BudgetMetrics, NullBudgetMetrics
from hiveplane.budget.models import BudgetSnapshot, CostAttribution
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.store import BudgetStore
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import BudgetCheck, BudgetLevel, BudgetOutcome, UsageReport
from hiveplane.core.workload import AgentWorkload


class BudgetService:
    """Prices usage and enforces run, day, and team budget limits."""

    def __init__(
        self,
        store: BudgetStore,
        pricing: CostTable,
        *,
        metrics: BudgetMetrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._pricing = pricing
        self._metrics = metrics or NullBudgetMetrics()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _today(self) -> str:
        return self._clock().date().isoformat()

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck:
        """Return the headroom check for admitting a run."""
        budget = workload.spec.budget
        day = self._today()
        day_limit = budget.per_day_usd
        day_spent = self._store.day_spend(workload.name, day)
        if day_spent >= day_limit:
            return BudgetCheck(
                allowed=False,
                level=BudgetLevel.DAY,
                limit_usd=day_limit,
                spent_usd=day_spent,
                remaining_usd=0.0,
                reason=f"day budget exhausted for {workload.name!r}",
            )
        team_limit = budget.per_team_usd
        if team_limit is not None and workload.team is not None:
            team_spent = self._store.team_spend(workload.team, day)
            if team_spent >= team_limit:
                return BudgetCheck(
                    allowed=False,
                    level=BudgetLevel.TEAM,
                    limit_usd=team_limit,
                    spent_usd=team_spent,
                    remaining_usd=0.0,
                    reason=f"team budget exhausted for {workload.team!r}",
                )
        return BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=budget.per_run_usd,
            spent_usd=0.0,
            remaining_usd=budget.per_run_usd,
        )

    def record_usage(self, workload: AgentWorkload, report: UsageReport) -> BudgetOutcome:
        """Price a usage event, accumulate spend, and return the run check."""
        if report.model_identity is not None:
            cost = self._pricing.price(
                report.model_identity, report.input_tokens, report.output_tokens
            )
        else:
            cost = report.cost_usd
        day = self._today()
        self._store.add_run_spend(report.run_id, cost)
        self._store.add_day_spend(workload.name, day, cost)
        if workload.team is not None:
            self._store.add_team_spend(workload.team, day, cost)
        self._store.record_attribution(
            CostAttribution(
                run_id=report.run_id,
                workload=workload.name,
                team=workload.team,
                model_identity=report.model_identity,
                input_tokens=report.input_tokens,
                output_tokens=report.output_tokens,
                tool_calls=report.tool_calls,
                cost_usd=cost,
                timestamp=report.timestamp,
            )
        )
        self._metrics.record_spend(cost, workload.name, workload.team)

        budget = workload.spec.budget
        run_spent = self._store.run_spend(report.run_id)
        if run_spent > budget.per_run_usd:
            self._metrics.record_exceeded(BudgetLevel.RUN, workload.name)
            return BudgetOutcome(check=self._exceeded(BudgetLevel.RUN, budget.per_run_usd), cost_usd=cost)
        day_spent = self._store.day_spend(workload.name, day)
        if day_spent > budget.per_day_usd:
            self._metrics.record_exceeded(BudgetLevel.DAY, workload.name)
            return BudgetOutcome(check=self._exceeded(BudgetLevel.DAY, budget.per_day_usd), cost_usd=cost)
        team_limit = budget.per_team_usd
        if team_limit is not None and workload.team is not None:
            team_spent = self._store.team_spend(workload.team, day)
            if team_spent > team_limit:
                self._metrics.record_exceeded(BudgetLevel.TEAM, workload.name)
                return BudgetOutcome(check=self._exceeded(BudgetLevel.TEAM, team_limit), cost_usd=cost)
        return BudgetOutcome(
            check=BudgetCheck(
                allowed=True,
                level=BudgetLevel.RUN,
                limit_usd=budget.per_run_usd,
                spent_usd=run_spent,
                remaining_usd=max(0.0, budget.per_run_usd - run_spent),
            ),
            cost_usd=cost,
        )

    def snapshot(self, workload: AgentWorkload, run_id: str) -> BudgetSnapshot:
        """Return current spend for a run, workload day, and team day."""
        day = self._today()
        team = workload.team
        return BudgetSnapshot(
            workload=workload.name,
            team=team,
            day=day,
            run_usd=self._store.run_spend(run_id),
            day_usd=self._store.day_spend(workload.name, day),
            team_usd=self._store.team_spend(team, day) if team is not None else 0.0,
        )

    @staticmethod
    def _exceeded(level: BudgetLevel, limit: float) -> BudgetCheck:
        return BudgetCheck(
            allowed=False,
            level=level,
            limit_usd=limit,
            spent_usd=limit,
            remaining_usd=0.0,
            reason=f"{level.value} budget exceeded",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_budget_service.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Full gate + commit**

```bash
git add src/hiveplane/budget/metrics.py src/hiveplane/budget/service.py tests/test_budget_service.py
git commit -m "feat(budget): add budget service with pricing and burn metrics"
git push origin main
```

---

### Task 5: RunService budget enforcement and wiring

**Files:**
- Modify: `src/hiveplane/execution/service.py`
- Modify: `src/hiveplane/execution/wiring.py`
- Modify: `src/hiveplane/api/app.py`
- Test: `tests/test_execution_budget.py`

**Interfaces:**
- Consumes: `execution.gates.BudgetGate`, `budget.service.BudgetService`, `core.usage.BudgetOutcome`.
- Produces:
  - `RunService(*, budget: BudgetGate | None = None, ...)`
  - `RunService.record_usage` fails the run when the budget check is not allowed
  - `build_run_service(registry_service, policy_gate, approvals, budget_gate)` and app state `budget_service`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for budget enforcement through the run service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.fanout import FanOutService
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox
from test_execution_escalation import _Executor


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _service(
    make_manifest: Callable[..., AgentWorkload], *, per_run: float = 1.0
) -> tuple[RunService, str]:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(name="agent-1", budget={"per_run_usd": per_run, "per_day_usd": 100.0})
    registry.create(workload)
    store = InMemoryRunStore()
    fanout = FanOutService(store, {}, clock=_clock)
    budget = BudgetService(InMemoryBudgetStore(), CostTable(), clock=_clock)
    admission = AdmissionPipeline(
        _Cert(True, "m1"), _Policy(DecisionOutcome.ALLOW), _Budget(True), _Sandbox(False), clock=_clock
    )
    ids = iter(["run-1"])
    service = RunService(
        store,
        registry,
        admission=admission,
        executor=_Executor(),
        fanout=fanout,
        budget=budget,
        clock=_clock,
        id_factory=lambda: next(ids),
    )
    return service, "agent-1"


def _report(tokens: int) -> UsageReport:
    return UsageReport(
        run_id="run-1",
        input_tokens=tokens,
        output_tokens=0,
        tool_calls=1,
        cost_usd=0.0,
        timestamp=_clock(),
        model_identity="openai/gpt-4o/2024-08-06",
    )


def test_usage_within_budget_updates_cost(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.SANDBOX)
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    updated = service.record_usage(run.id, _report(tokens=1000))
    assert updated.state is RunState.RUNNING
    assert updated.cost_usd > 0


def test_over_budget_usage_fails_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, workload = _service(make_manifest, per_run=0.001)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.SANDBOX)
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    failed = service.record_usage(run.id, _report(tokens=1000))
    assert failed.state is RunState.FAILED
    assert failed.failure_reason is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_execution_budget.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'budget'`

- [ ] **Step 3: Write minimal implementation**

`src/hiveplane/execution/service.py`:
- import the budget seam: `from hiveplane.execution.gates import ApprovalRequests, BudgetGate, FanOut, RunExecutor`
- add constructor param `budget: BudgetGate | None = None` stored as `self._budget`
- enrich the report with the run's model identity and enforce the budget in `record_usage`:

```python
    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        """Record usage for a run, enforce budget, and update accumulated cost."""
        run = self._require(run_id)
        workload = self._registry.get(run.workload_id).manifest
        if report.model_identity is None and run.model_identity is not None:
            report = report.model_copy(update={"model_identity": run.model_identity})
        self._store.add_usage(report)
        self._append_event(run_id, EventType.USAGE, "adapter", detail=str(report.cost_usd))
        cost = report.cost_usd
        check = None
        if self._budget is not None:
            outcome = self._budget.record_usage(workload, report)
            cost = outcome.cost_usd
            check = outcome.check
        updated = run.model_copy(
            update={"cost_usd": run.cost_usd + cost, "updated_at": self._clock()}
        )
        self._store.save_run(updated)
        if check is not None and not check.allowed:
            return self.fail(run_id, actor="budget", reason=check.reason or "budget exceeded")
        return updated
```

`src/hiveplane/execution/wiring.py`:
- add `budget_gate: BudgetGate` param
- pass it to both `AdmissionPipeline` and `RunService`:

```python
def build_run_service(
    registry_service: RegistryService,
    policy_gate: PolicyGate,
    approvals: ApprovalRequests,
    budget_gate: BudgetGate,
) -> RunService:
    ...
        admission=AdmissionPipeline(
            RegistryCertificationGate(registry_service),
            policy_gate,
            budget_gate,
            ManifestSandboxGate(),
        ),
        executor=NullRunExecutor(),
        fanout=fanout,
        approvals=approvals,
        budget=budget_gate,
    )
```

`src/hiveplane/api/app.py`:
- import `BudgetService`, `CostTable`, `InMemoryBudgetStore`
- build it and pass to `build_run_service`; store on app state:

```python
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.budget.errors import UnknownModelPriceError
```

```python
    budget_service = BudgetService(InMemoryBudgetStore(), CostTable())
    app.state.budget_service = budget_service
    app.state.run_service = run_service or build_run_service(
        registry, policy_engine, approval_service, budget_service
    )
```

And map `UnknownModelPriceError` to 422 in the error handlers.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_execution_budget.py -v`
Expected: PASS (2 tests). Then run the whole suite; the phase-1/2 tests must still pass (the `_Budget` fake and `UnlimitedBudgetGate` were updated in Task 1).

- [ ] **Step 5: Full exit gate + docs + commit**

```bash
.venv/bin/python -m pytest --cov=src/hiveplane --cov-report=term-missing -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src/ tests/
```

Coverage must exceed 95%. Update `docs/wbs/v0.1.0/wbs-v0.1.0-part6-budget.md` with a status note and add a short budget section to `docs/USER_GUIDE.md`.

```bash
git add src/hiveplane/execution/service.py src/hiveplane/execution/wiring.py src/hiveplane/api/app.py tests/test_execution_budget.py docs/USER_GUIDE.md docs/wbs/v0.1.0/wbs-v0.1.0-part6-budget.md
git commit -m "feat(budget): enforce budget during run execution and wire the service"
git push origin main
```

---

## Self-review

**Spec coverage** (against `docs/design/budget-enforcement-design.md`):

| Requirement | Task |
|-------------|------|
| Per-model cost table; known tokens map to expected cost | 2 |
| Unknown models fail loudly | 2 |
| Per-run, per-day, per-team limits | 4 |
| Usage events accumulate spend and attribution | 3, 4 |
| Over-budget run blocked or escalated | 4, 5 |
| Budget-burn metrics emitted | 4 |
| Admission-time budget check | 4 (via existing admission pipeline `check`) |

Cost showback (CPCT, waste, ROI, spend views) is deferred to the cost-service effort; this plan records attribution and a snapshot.

**Placeholder scan:** no `TODO`/`TBD`/"handle edge cases"/"similar to Task N". Every code step is complete.

**Type consistency:** `BudgetOutcome` is defined in Task 1 and returned by `UnlimitedBudgetGate` and `BudgetService.record_usage` in Tasks 1/4. `BudgetGate.record_usage(workload, report)` matches `RunService` usage in Task 5. `BudgetLevel` is reused from `core.usage`. `CostAttribution`/`BudgetSnapshot` are defined once in Task 3 and used in Tasks 4/5.

**Known coupling:** the budget seam signature changes in Task 1, and the phase-1 `_Budget` fake and `UnlimitedBudgetGate` test are updated in the same task so the suite stays green before the real service lands.

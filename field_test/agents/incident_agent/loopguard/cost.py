"""Cost tracking for per-completed-task metrics.

This module implements SPEC §6.1 — CostTracker, the component responsible
for tracking cost per completed task (not per-call). The key distinction
from cost trackers like agentcost-sdk is that loopguard's cost metric is
per-completed-task, not per-call: a task that loops 10 times costs more
than one that succeeds on the first call. The "cheap" per-call price is
misleading.

Design:
    - TaskCost: per-task cost record (retries, escalations, tokens, cost)
    - CostTracker: aggregates TaskCost records across tasks
    - Escalation rate: escalations / total guarded calls
    - Routing vs failover separation (FR-3.3): routing = intentional
      escalation based on failure patterns; failover = availability
      failure (model unavailability, network errors, etc.)

Thread safety:
    CostTracker uses a threading.Lock to protect its internal dict,
    making it safe for concurrent use across tasks.

Implemented in milestone S6 per the WBS.
"""

# CostTracker separates retry costs (workhorse model) from escalation costs
# (stronger model).  This is essential for the routing-vs-failover metric:
# routing = intentional escalation based on failure patterns;
# failover = escalation due to model unavailability.
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class TaskCost:
    """Per-task cost record aggregated across steps and escalations.

    A single guarded call (task) accumulates costs from all its retry
    steps and any escalation calls triggered by the task.

    Attributes:
        task_id: Unique identifier for this task (e.g., a UUID).
        retries: Number of retry steps recorded.
        escalations: Number of escalation calls made.
        total_tokens: Cumulative tokens consumed by retries + escalations.
        total_cost_usd: Cumulative cost in USD.
        escalation_categories: Count of each escalation category
            (e.g., {"routing": 1, "failover": 0}).

    """

    task_id: str
    retries: int = 0
    escalations: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    escalation_categories: dict[str, int] = field(default_factory=dict[str, int])


class CostTracker:
    """Tracks cost per completed task, escalation rate, and routing vs failover.

    The tracker aggregates TaskCost records across all guarded calls
    made through a Guard instance. Metrics are computed on demand from
    the stored records.

    Typical usage::

        tracker = CostTracker()

        # After each agent step
        tracker.record_step("task-1", tokens=150, cost_usd=0.003)

        # After each escalation
        tracker.record_escalation("task-1", tokens=500, cost_usd=0.01)

        # Query metrics
        cost = tracker.get_cost_per_completed_task("task-1")
        rate = tracker.get_escalation_rate()
        breakdown = tracker.get_routing_vs_failover()

    """

    def __init__(self) -> None:
        """Initialise an empty CostTracker."""
        self._tasks: dict[str, TaskCost] = {}
        self._total_guarded_calls: int = 0
        self._lock: Lock = Lock()

    # Retry vs escalation separation: record_step tracks workhorse model calls
    # (retries), while record_escalation tracks the stronger model.  This lets
    # the CostTracker compute per-task cost and distinguish between "task had
    # many retries before succeeding" vs "task required escalation."
    def record_step(
        self,
        task_id: str,
        tokens: int,
        cost_usd: float,
    ) -> None:
        """Record a retry step for a task.

        Creates a TaskCost record for the task if one does not exist
        (lazy initialisation). Increments the retry count and adds
        tokens/cost.

        Args:
            task_id: Unique identifier for the task.
            tokens: Tokens consumed by this step.
            cost_usd: Cost in USD of this step.

        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                task = TaskCost(task_id=task_id)
                self._tasks[task_id] = task
            task.retries += 1
            task.total_tokens += tokens
            task.total_cost_usd += cost_usd

    def record_escalation(
        self,
        task_id: str,
        tokens: int,
        cost_usd: float,
        escalation_category: str = "routing",
    ) -> None:
        """Record an escalation call for a task.

        Creates a TaskCost record for the task if one does not exist.
        Increments the escalation count, adds tokens/cost, and tracks
        the escalation category for routing vs failover separation
        (FR-3.3).

        Args:
            task_id: Unique identifier for the task.
            tokens: Tokens consumed by the escalation model call.
            cost_usd: Cost in USD of the escalation call.
            escalation_category: "routing" (intentional escalation due
                to failure pattern) or "failover" (availability failure).

        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                task = TaskCost(task_id=task_id)
                self._tasks[task_id] = task
            task.escalations += 1
            task.total_tokens += tokens
            task.total_cost_usd += cost_usd
            task.escalation_categories[escalation_category] = (
                task.escalation_categories.get(escalation_category, 0) + 1
            )

    def record_guarded_call(self) -> None:
        """Increment the total guarded call counter.

        This counter tracks the total number of guarded function calls
        (each invocation of a @guard.protect or @guard.aprotect wrapped
        function) for computing the escalation rate.

        """
        with self._lock:
            self._total_guarded_calls += 1

    def get_cost_per_completed_task(
        self,
        task_id: str,
    ) -> float | None:
        """Return the total cost for a task, or None if the task has not completed.

        Total cost = all retries + failed loops + escalation calls.
        A task is considered "completed" if it has at least one retry
        or escalation recorded.

        The formula is:
            cost = Σ(retry_step_tokens) * model_price + Σ(escalation_tokens) * escalation_price
        In v0.1.0, pricing is not tracked per-model — users should multiply
        the returned cost by their model's per-token price externally.

        Args:
            task_id: The task to query.

        Returns:
            Total cost in USD, or None if the task has no recorded data.

        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return task.total_cost_usd

    def get_escalation_rate(self) -> float:
        """Return the escalation rate as a float between 0.0 and 1.0.

        Escalation rate = total escalations across all tasks / total
        guarded calls. Returns 0.0 if no guarded calls have been
        recorded.

        Returns:
            Escalation rate (0.0 — 1.0).

        """
        with self._lock:
            if self._total_guarded_calls == 0:
                return 0.0
            total_escalations = sum(
                t.escalations for t in self._tasks.values()
            )
            return total_escalations / self._total_guarded_calls

    def get_routing_vs_failover(self) -> dict[str, int]:
        """Return the count of routing vs failover escalations.

        Routing = intentional escalation triggered by a failure pattern
        (e.g., repeated_error, test_failure, schema_invalid).
        Failover = escalation caused by model unavailability or
        infrastructure failure (e.g., the escalation model itself
        errors out, triggering a fail-open scenario).

        SPEC §6.1 / FR-3.3.

        Returns:
            Dict with keys "routing" and "failover" mapping to counts.

        """
        with self._lock:
            routing = 0
            failover = 0
            for task in self._tasks.values():
                routing += task.escalation_categories.get("routing", 0)
                failover += task.escalation_categories.get("failover", 0)
            return {"routing": routing, "failover": failover}

    def clear(self) -> None:
        """Reset all tracked data.

        Removes all task records and resets the guarded call counter.
        Useful for testing or between independent benchmark runs.

        """
        with self._lock:
            self._tasks.clear()
            self._total_guarded_calls = 0

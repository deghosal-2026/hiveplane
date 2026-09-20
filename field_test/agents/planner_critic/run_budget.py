from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from planner_critic.reason_codes import (
    AMBIGUOUS_REPLAN_ESCALATED,
    DETERMINISTIC_REPLAN_TRIGGERED,
    RUN_BUDGET_EXCEEDED,
    RUN_DEPTH_EXCEEDED,
    RUN_TIMEOUT,
    TRANSIENT_RETRY_TRIGGERED,
)
from planner_critic.types import ExecutionTrace

logger = logging.getLogger(__name__)


class FailureClass(StrEnum):
    TRANSIENT = "transient"
    DETERMINISTIC = "deterministic"
    AMBIGUOUS = "ambiguous"


REASON_CODE_MAP: dict[FailureClass, str] = {
    FailureClass.TRANSIENT: TRANSIENT_RETRY_TRIGGERED,
    FailureClass.DETERMINISTIC: DETERMINISTIC_REPLAN_TRIGGERED,
    FailureClass.AMBIGUOUS: AMBIGUOUS_REPLAN_ESCALATED,
}


@dataclass
class RunBudget:
    run_max_budget_usd: float | None = None
    run_max_depth: int | None = None
    run_max_time: float | None = None
    _cumulative_spend_usd: float = 0.0
    _cascading_depth: int = 0
    _started_at: float = field(default_factory=time.monotonic)
    _exceeded: str | None = None

    @property
    def exceeded(self) -> str | None:
        return self._exceeded

    def record_spend(self, usd: float) -> None:
        self._cumulative_spend_usd += usd
        if (
            self.run_max_budget_usd is not None
            and self._cumulative_spend_usd > self.run_max_budget_usd
        ):
            self._exceeded = RUN_BUDGET_EXCEEDED
            logger.info(
                "run budget: spend $%.4f exceeds ceiling $%.4f",
                self._cumulative_spend_usd,
                self.run_max_budget_usd,
            )

    def record_replan(self) -> None:
        self._cascading_depth += 1
        if self.run_max_depth is not None and self._cascading_depth > self.run_max_depth:
            self._exceeded = RUN_DEPTH_EXCEEDED
            logger.info(
                "run budget: cascading depth %d exceeds ceiling %d",
                self._cascading_depth,
                self.run_max_depth,
            )

    def check_timeout(self) -> str | None:
        if self.run_max_time is None:
            return None
        elapsed = time.monotonic() - self._started_at
        if elapsed > self.run_max_time:
            self._exceeded = RUN_TIMEOUT
            logger.info(
                "run budget: elapsed %.1fs exceeds ceiling %.1fs", elapsed, self.run_max_time
            )
            return RUN_TIMEOUT
        return None

    def check(self) -> str | None:
        timeout = self.check_timeout()
        if timeout:
            return timeout
        return self._exceeded


class ReplanClassifier:
    TRANSIENT_CODES: ClassVar[set[str]] = {
        "timeout",
        "rate_limit",
        "network_error",
        "connection_reset",
        "service_unavailable",
        "429",
        "503",
    }
    DETERMINISTIC_CODES: ClassVar[set[str]] = {
        "precondition_drift",
        "schema_mismatch",
        "missing_dependency",
        "invalid_config",
        "auth_failure",
        "permission_denied",
        "not_found",
    }

    def __init__(self, step_max_retries: int = 3) -> None:
        self.step_max_retries = step_max_retries
        self._step_retries: dict[str, int] = {}

    def classify(self, trace: ExecutionTrace) -> FailureClass:
        err = (trace.outcome or "").lower().strip()
        if any(code in err for code in self.TRANSIENT_CODES):
            retries = self._step_retries.get(trace.task_id, 0) + 1
            self._step_retries[trace.task_id] = retries
            if retries > self.step_max_retries:
                return FailureClass.AMBIGUOUS
            return FailureClass.TRANSIENT
        if any(code in err for code in self.DETERMINISTIC_CODES):
            return FailureClass.DETERMINISTIC
        return FailureClass.AMBIGUOUS

    def check_step_retry_exceeded(self, task_id: str) -> bool:
        return self._step_retries.get(task_id, 0) > self.step_max_retries

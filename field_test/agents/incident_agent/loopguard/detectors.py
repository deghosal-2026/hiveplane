"""Failure detection and trigger implementations.

FailureDetector monitors GuardState steps and determines whether a
trigger condition is met. Four trigger types:

- repeated_error (FR-1.1): Same error_type + error_message across N steps
- test_failure (FR-1.2): Same test fails N consecutive times or oscillates
- schema_invalid (FR-1.3): schema_valid flag is False for N consecutive steps
- custom (FR-1.5): User-provided callback returns True

Trigger evaluation order per SPEC §3.3:
1. custom (user-defined, highest priority)
2. repeated_error (string hash comparison, cheapest)
3. test_failure (dict comparison)
4. schema_invalid (flag check)

First trigger to fire wins. Only one escalation per evaluation cycle.

Design decisions (from S3 review):
- TRIGGER_ORDER is explicit, not derived from dict insertion order
  (SPEC §3.1 uses a loop over dict items, but dict order is fragile).
- test_failure and schema_invalid skip steps where the tracked field
  is None (test_results=None or schema_valid=None).  Steps without
  data are not "failing" and should not break the consecutive-failure
  chain.
- repeated_error checks the last N *total* steps: a success step
  between errors breaks the chain.  This enforces strict consecutiveness
  per the SPEC phrase "appears max_retries consecutive times".
- Oscillation detection uses a fixed 4-step window, independent of
  max_retries, because the pass→fail→pass→fail pattern is defined
  by SPEC §3.2 as a separate detection mode (not threshold-based).
"""

from collections.abc import Mapping
from typing import Any

# TriggerResult is used instead of exceptions for trigger detection because
# exceptions would use try/except control flow for a non-error condition.
# Returning None for "no trigger" is simpler and allows the caller (Guard)
# to treat triggers as value objects rather than control-flow mechanisms.
from ai_loopguard._internal.state import GuardState, StepRecord, TriggerResult
from ai_loopguard.config import TriggerConfig
from ai_loopguard.exceptions import TriggerError

# SPEC §3.3 evaluation order — hardcoded for explicitness.
# The SPEC §3.1 pseudocode uses a loop over config dict which relies
# on Python 3.7+ insertion-order guarantees; we choose to be explicit.
_TRIGGER_ORDER: tuple[str, ...] = (
    "custom",
    "repeated_error",
    "test_failure",
    "schema_invalid",
)

# Fixed 4-step window for oscillation detection (SPEC §3.2).
# The pass→fail→pass→fail pattern is a defined pattern, not a
# threshold-based check, so it is independent of max_retries.
_OSCILLATION_WINDOW: int = 4


class FailureDetector:
    """Monitors GuardState steps and detects stuck patterns.

    Attributes:
        _trigger_configs: Dict of trigger name -> TriggerConfig. Controls
            which triggers are enabled and their retry thresholds.

    """

    # The constructor accepts both TriggerConfig objects and plain dicts.
    # This deviates from the SPEC §3.1 signature (dict[str, TriggerConfig])
    # for user convenience — callers can pass dicts directly without
    # constructing TriggerConfig objects first.
    def __init__(
        self,
        config: Mapping[str, "TriggerConfig | dict[str, Any]"],
    ) -> None:
        """Initialise detector with trigger configurations.

        Args:
            config: Dict mapping trigger names to TriggerConfig objects
                or plain dicts (which are coerced to TriggerConfig).
                Expected keys: "repeated_error", "test_failure",
                "schema_invalid", "custom".

        """
        # Coerce plain dicts to TriggerConfig — user convenience so callers
        # can pass raw dicts from YAML/TOML without constructing objects.
        self._trigger_configs: dict[str, TriggerConfig] = {}
        for name, cfg in config.items():
            if isinstance(cfg, TriggerConfig):
                self._trigger_configs[name] = cfg
            else:
                self._trigger_configs[name] = TriggerConfig(**cfg)

    @property
    def trigger_configs(self) -> dict[str, TriggerConfig]:
        """Return a shallow copy of the internal trigger configuration dict.

        The returned dict is a new mapping, but the TriggerConfig values
        are shared references — mutating a returned TriggerConfig affects
        the detector's internal state.
        """
        return dict(self._trigger_configs)

    # Convenience constructor: builds the standard three trigger configs
    # (repeated_error, test_failure, schema_invalid) all enabled at the
    # given thresholds.  Trade-off: bypasses GuardConfig.triggers, so users
    # who construct a FailureDetector directly get the same defaults
    # without needing a full GuardConfig.  Useful for standalone testing.
    @classmethod
    def from_defaults(
        cls,
        repeated_error_max_retries: int = 3,
        test_failure_max_retries: int = 3,
        schema_invalid_max_retries: int = 3,
    ) -> "FailureDetector":
        """Create a FailureDetector with default trigger configs.

        Builds the standard three trigger configs (repeated_error,
        test_failure, schema_invalid) all enabled. Custom retry
        thresholds can be overridden per trigger type.

        Args:
            repeated_error_max_retries: Max retries for repeated_error.
            test_failure_max_retries: Max retries for test_failure.
            schema_invalid_max_retries: Max retries for schema_invalid.

        Returns:
            A new FailureDetector instance with the given thresholds.

        """
        return cls(
            config={
                "repeated_error": TriggerConfig(
                    enabled=True,
                    max_retries=repeated_error_max_retries,
                ),
                "test_failure": TriggerConfig(
                    enabled=True,
                    max_retries=test_failure_max_retries,
                ),
                "schema_invalid": TriggerConfig(
                    enabled=True,
                    max_retries=schema_invalid_max_retries,
                ),
            },
        )

    def check(self, state: GuardState) -> TriggerResult | None:
        """Run all enabled triggers against the current guard state.

        Evaluation order per SPEC §3.3: custom -> repeated_error ->
        test_failure -> schema_invalid.  First trigger to fire wins.

        Enabled checks are centralized here (SPEC §3.1), not scattered
        across the _check_* methods.

        Unknown trigger names (not in _TRIGGER_ORDER) are silently
        skipped.  To add a custom trigger, use the "custom" trigger
        with a custom_callback — do not add new trigger names.

        Args:
            state: The current GuardState to inspect.

        Returns:
            A TriggerResult if a trigger fires, or None.

        """
        # O(N) over trigger names (currently 4, bounded by config dict size).
        # An O(1) dict lookup per trigger would be possible but N is small enough
        # that the dispatch dict overhead is not worth the complexity.
        dispatch = {
            "custom": self._check_custom,
            "repeated_error": self._check_repeated_error,
            "test_failure": self._check_test_failure,
            "schema_invalid": self._check_schema_invalid,
        }
        for trigger_name in _TRIGGER_ORDER:
            if trigger_name not in self._trigger_configs:
                continue
            config = self._trigger_configs[trigger_name]
            if not config.enabled:
                continue
            impl = dispatch.get(trigger_name)
            if impl is None:
                continue
            result = impl(config, state)
            if result is not None:
                return result
        return None

    # ── _check_repeated_error (FR-1.1) ─────────────────────────────────

    def _check_repeated_error(
        self,
        config: TriggerConfig,
        state: GuardState,
    ) -> TriggerResult | None:
        """Check for repeated identical errors (FR-1.1).

        Looks at the last max_retries *total* steps.  If all of them
        have the same error_type + error_message, the trigger fires.
        A success step (no error) among them breaks the chain — this
        enforces the SPEC's "consecutive times" contract.

        Args:
            config: TriggerConfig for repeated_error (already enabled).
            state: Current GuardState.

        Returns:
            TriggerResult if the trigger fires, else None.

        """
        # Uses tuple comparison of (error_type, error_message) across the
        # last N steps — effectively a rolling equality check.  This avoids
        # a persistent rolling hash which would need invalidation logic.
        max_retries = config.max_retries
        steps = state.steps

        if len(steps) < max_retries:
            return None

        # Slice the last max_retries *total* steps (not filtered by error
        # presence).  A success step (error_type=None) among them breaks
        # the chain — this enforces strict consecutiveness per SPEC §3.1.
        recent = steps[-max_retries:]

        for s in recent:
            if s.error_type is None or s.error_message is None:
                return None

        # Compare all N steps to the first.  If any differs, the pattern
        # is not "repeated identical error."
        first = recent[0]
        for s in recent[1:]:
            if (s.error_type, s.error_message) != (
                first.error_type,
                first.error_message,
            ):
                return None

        detail = (
            f"{first.error_type}: {first.error_message}, "
            f"{max_retries} consecutive"
        )
        return TriggerResult(
            trigger_name="repeated_error",
            detail=detail,
            retry_count=max_retries,
        )

    # ── _check_test_failure (FR-1.2) ───────────────────────────────────

    def _check_test_failure(
        self,
        config: TriggerConfig,
        state: GuardState,
    ) -> TriggerResult | None:
        """Check for failing tests (FR-1.2).

        Dispatches to two detection modes:
        1. Consecutive failure: same test fails max_retries times.
        2. Oscillation: same test alternates pass/fail over 4 steps.

        Steps without test_results are skipped — they neither count
        toward the consecutive-fail chain nor break it.  This is
        intentional: a step where the user didn't record results is
        not a "failing" step and should not reset the counter.

        Args:
            config: TriggerConfig for test_failure (already enabled).
            state: Current GuardState.

        Returns:
            TriggerResult if the trigger fires, else None.

        """
        steps = state.steps
        # Skip steps without test_results.  A step where no tests were
        # recorded is NOT a "failing" step — it should not count toward
        # the consecutive-fail chain, nor should it break the chain.
        test_steps = [s for s in steps if s.test_results is not None]

        if len(test_steps) < config.max_retries:
            return None

        result = self._check_test_failure_consecutive(
            config, test_steps
        )
        if result is not None:
            return result

        return self._check_test_failure_oscillation(
            config, test_steps
        )

    def _check_test_failure_consecutive(
        self,
        config: TriggerConfig,
        test_steps: list[StepRecord],
    ) -> TriggerResult | None:
        """Mode 1: same test fails max_retries consecutive times."""
        max_retries = config.max_retries

        if len(test_steps) < max_retries:
            return None

        recent = test_steps[-max_retries:]
        # Compute the intersection of test names across all recent steps.
        # A test must appear in EVERY step to be a candidate — tests in only
        # some steps cannot have failed "consecutively."
        common_tests: set[str] | None = None
        for s in recent:
            keys = set(s.test_results.keys())  # type: ignore[union-attr]
            if common_tests is None:
                common_tests = keys
            else:
                common_tests &= keys

        if common_tests:
            for test_name in common_tests:
                # Re-check test_results is not None for the type checker.
                # The list comprehension above already filtered None values,
                # but mypy cannot infer that across the comprehension boundary.
                all_false = all(
                    s.test_results is not None
                    and s.test_results.get(test_name) is False
                    for s in recent
                )
                if all_false:
                    msg = (
                        f"test '{test_name}' failed "
                        f"{max_retries} consecutive times"
                    )
                    return TriggerResult(
                        trigger_name="test_failure",
                        detail=msg,
                        retry_count=max_retries,
                    )

        return None

    def _check_test_failure_oscillation(
        self,
        config: TriggerConfig,
        test_steps: list[StepRecord],
    ) -> TriggerResult | None:
        """Mode 2: oscillation detection (pass -> fail -> pass -> fail).

        Always examines the last _OSCILLATION_WINDOW (4) steps with
        test_results, independent of max_retries.  The oscillation
        pattern is defined by SPEC §3.2: alternation between pass
        and fail over successive steps.

        """
        # Fixed 4-step window for pass→fail→pass→fail detection.
        # Independent of max_retries because oscillation is a pattern,
        # not a threshold — a test oscillating 3 times is still oscillating.
        if len(test_steps) < _OSCILLATION_WINDOW:
            return None

        last_four = test_steps[-_OSCILLATION_WINDOW:]
        # Same intersection logic as consecutive mode: a test must appear in
        # all 4 steps to be a candidate for oscillation detection.
        oscillating_tests: set[str] | None = None
        for s in last_four:
            keys = set(s.test_results.keys())  # type: ignore[union-attr]
            if oscillating_tests is None:
                oscillating_tests = keys
            else:
                oscillating_tests &= keys

        if oscillating_tests:
            for test_name in oscillating_tests:
                # Treat missing test results as False (fail).  bool(None)
                # is False, so missing keys and explicit None map to failure.
                values: list[bool] = [
                    bool(
                        s.test_results is not None
                        and s.test_results.get(test_name)
                    )
                    for s in last_four
                ]
                # all() on adjacent-inequality checks for strict alternation.
                # pass→fail→pass→fail produces [True,False,True,False];
                # fail→pass→fail→pass produces [False,True,False,True].
                # Any run of two same values breaks the pattern.
                alternating = all(
                    values[i] != values[i - 1]
                    for i in range(1, len(values))
                )
                if alternating:
                    pattern = (
                        "pass -> fail -> pass -> fail"
                        if values[0]
                        else "fail -> pass -> fail -> pass"
                    )
                    detail = (
                        f"test '{test_name}' oscillating: {pattern}"
                    )
                    # retry_count is the window length, not max_retries,
                    # because the oscillation was detected over 4 steps.
                    return TriggerResult(
                        trigger_name="test_failure",
                        detail=detail,
                        retry_count=_OSCILLATION_WINDOW,
                    )

        return None

    # ── _check_schema_invalid (FR-1.3) ─────────────────────────────────

    def _check_schema_invalid(
        self,
        config: TriggerConfig,
        state: GuardState,
    ) -> TriggerResult | None:
        """Check for repeated schema validation failures (FR-1.3).

        Looks at the last max_retries steps that have schema_valid set.
        Steps where schema_valid is None are skipped — they neither
        count as failures nor break the chain.  This is intentional:
        a step where validation wasn't performed should not reset the
        consecutive-fail counter.

        Args:
            config: TriggerConfig for schema_invalid (already enabled).
            state: Current GuardState.

        Returns:
            TriggerResult if the trigger fires, else None.

        """
        max_retries = config.max_retries
        steps = state.steps

        if len(steps) < max_retries:
            return None

        # Walk backwards to collect recent steps with schema_valid set.
        # Unlike repeated_error (which checks total steps), schema_invalid
        # skips None steps — a step where validation wasn't performed is
        # not a "failing" step and should not reset the counter.
        schema_steps: list[StepRecord] = []
        for s in reversed(steps):
            if s.schema_valid is not None:
                schema_steps.append(s)
                if len(schema_steps) == max_retries:
                    break

        if len(schema_steps) < max_retries:
            return None

        # Boolean reset: schema_valid=False is the failure signal.
        # Using `is False` avoids matching None (which was already filtered).
        # This is a design choice — None means "not validated" (skip),
        # not "validated and invalid" (count as failure).
        if all(s.schema_valid is False for s in schema_steps):
            detail = (
                f"schema invalid for {max_retries} consecutive steps"
            )
            return TriggerResult(
                trigger_name="schema_invalid",
                detail=detail,
                retry_count=max_retries,
            )

        return None

    # ── _check_custom (FR-1.5) ────────────────────────────────────────

    def _check_custom(
        self,
        config: TriggerConfig,
        state: GuardState,
    ) -> TriggerResult | None:
        """Check custom user-defined trigger callback (FR-1.5).

        Calls config.custom_callback(state).  If the callback returns
        True, the trigger fires with retry_count=0 (custom callbacks
        have no retry count — the callback fires instantly when its
        condition is met).

        Args:
            config: TriggerConfig for custom trigger (already enabled).
            state: Current GuardState.

        Returns:
            TriggerResult if the callback returns True, else None.

        Raises:
            TriggerError: If the custom callback raises an exception.

        """
        # Guard against None callback — a custom trigger with
        # custom_callback=None silently never fires.
        if config.custom_callback is None:
            return None

        # Custom callbacks receive the full GuardState for maximum
        # flexibility.  Trade-off: exposes internal state shape but
        # avoids needing an additional abstraction layer for custom
        # detection logic.
        try:
            result = config.custom_callback(state)
        except Exception as exc:
            # TriggerError wraps the callback exception rather than
            # letting it propagate raw.  This keeps the exception
            # hierarchy clean: callers catch TriggerError for all
            # trigger-related failures (vs EscalationError for
            # escalation model failures).
            msg = f"custom trigger callback raised: {exc}"
            raise TriggerError(msg) from exc

        if result:
            # retry_count=0: custom triggers have no threshold — they fire
            # instantly when the callback returns True.  The 0 distinguishes
            # custom triggers from threshold-based triggers in logs.
            detail = "custom trigger callback returned True"
            return TriggerResult(
                trigger_name="custom",
                detail=detail,
                retry_count=0,
            )

        return None

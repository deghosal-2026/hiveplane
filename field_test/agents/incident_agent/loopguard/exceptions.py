"""Exception hierarchy for loopguard.

All custom exceptions inherit from LoopguardError so callers can
catch a single base type or handle specific failure modes independently.

Hierarchy::

    LoopguardError (base)
    ├── EscalationError          # escalation model call failed
    ├── EscalationCapError       # max_escalations_per_run hit
    ├── TriggerError             # trigger callback raised
    ├── ConfigError              # invalid configuration
    └── ContextError             # context packaging/sanitization/redaction failed
"""


# Base class for all loopguard errors.  Users who use Guard as a black box
# can catch LoopguardError to handle all failure modes uniformly.
# Internal code (detectors, escalation manager) uses the specific subclasses
# to distinguish between trigger failures and escalation failures.
class LoopguardError(Exception):
    """Base exception for all loopguard errors.

    Catch this to handle any loopguard-related issue in a single
    except clause, or catch a specific subclass for targeted handling.
    """


# EscalationError vs TriggerError: two distinct failure modes.
# EscalationError = the escalation MODEL failed (network error, timeout).
# TriggerError = the trigger CALLBACK failed (user code exception).
# This separation lets callers handle them differently — trigger errors
# should surface during development, escalation errors in production.
class EscalationError(LoopguardError):
    """Raised when the escalation model call fails (e.g., network error, timeout).

    This means the *escalation* model itself errored — not the original
    workhorse model. The EscalationManager catches this and triggers
    fail-open behaviour per the on_guard_error config.
    """


# Also export under the SPEC name for backwards compatibility
# SPEC §16 originally used "EscalationCapReached" — alias kept so
# early adopters and docs referencing the old name still work.
EscalationCapReached = None  # placeholder; set after class def below


class EscalationCapError(LoopguardError):
    """Raised when max_escalations_per_run is reached for a guarded call.

    This prevents unbounded escalation costs (T6 mitigation). When the
    cap is hit, loopguard logs a CappedEvent and returns the last
    known output instead of calling the escalation model again.
    """


# Backwards-compatible alias matching SPEC §16 original naming
EscalationCapReached = EscalationCapError


class TriggerError(LoopguardError):
    """Raised when a custom trigger callback raises an exception.

    Custom callbacks are user-provided functions that receive GuardState
    and return bool. If the callback itself throws, loopguard wraps the
    error in TriggerError to keep the exception hierarchy clean.
    """


class ConfigError(LoopguardError):
    """Raised when GuardConfig validation fails on construction.

    Pydantic ValidationError is raised for field-level validation
    (e.g., max_retries out of range). ConfigError is raised for
    semantic errors that Pydantic can't catch.
    """


class ContextError(LoopguardError):
    """Raised when context packaging, sanitization, or redaction fails.

    This covers failures in ContextPackager.package(), Sanitizer.sanitize(),
    or Redactor.redact() — e.g., a user-supplied regex pattern is invalid.
    """

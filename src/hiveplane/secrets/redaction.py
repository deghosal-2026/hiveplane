"""Redaction of resolved secret values across every sink (M45-03).

Resolved plaintext is registered with a per-run :class:`Redactor`. Every sink
(logs, traces, audit, fan-out, artifacts) must pass text through the redactor,
and :meth:`Redactor.assert_absent` fails closed if a cleared value is ever found.
"""

from __future__ import annotations

import logging

from hiveplane.secrets.models import SecretLeakError

_REDACTED = "[REDACTED]"


class Redactor:
    """Tracks live secret values and redacts them from text."""

    def __init__(self) -> None:
        self._values: list[str] = []

    def register(self, value: str) -> None:
        """Register a plaintext secret to be redacted while it is live."""
        if value and value not in self._values:
            self._values.append(value)

    def unregister(self, value: str) -> None:
        """Stop redacting a value (e.g. after the run's secret is released)."""
        if value in self._values:
            self._values.remove(value)

    def redact(self, text: str) -> str:
        """Replace every live secret value with a placeholder."""
        redacted = text
        for value in self._values:
            if value and value in redacted:
                redacted = redacted.replace(value, _REDACTED)
        return redacted

    def contains(self, text: str) -> bool:
        """Return True if any live secret value appears in ``text``."""
        return any(value and value in text for value in self._values)

    def assert_absent(self, text: str, sink: str) -> None:
        """Fail closed if a secret value is present in ``sink``."""
        if self.contains(text):
            raise SecretLeakError(sink)

    def clear(self) -> None:
        """Forget all registered values."""
        self._values.clear()

    def __len__(self) -> int:
        return len(self._values)


class RedactorRegistry:
    """Per-run redactors so a secret is cleared when its run ends."""

    def __init__(self) -> None:
        self._redactors: dict[str, Redactor] = {}

    def for_run(self, run_id: str) -> Redactor:
        """Return (creating if needed) the redactor for a run."""
        redactor = self._redactors.get(run_id)
        if redactor is None:
            redactor = Redactor()
            self._redactors[run_id] = redactor
        return redactor

    def release(self, run_id: str) -> None:
        """Clear and forget a run's redactor."""
        redactor = self._redactors.pop(run_id, None)
        if redactor is not None:
            redactor.clear()

    def clear(self) -> None:
        """Forget every run's redactor."""
        for redactor in self._redactors.values():
            redactor.clear()
        self._redactors.clear()


class RedactionLogFilter(logging.Filter):
    """A logging filter that redacts secret values from log records."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the message and args in place; never drop records."""
        record.msg = self._redactor.redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: self._redactor.redact(str(value))
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(self._redactor.redact(str(arg)) for arg in record.args)
        return True

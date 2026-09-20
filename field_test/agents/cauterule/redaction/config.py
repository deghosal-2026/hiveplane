"""Custom pattern config for redaction."""

from __future__ import annotations

from cauterule.config import Config


def get_custom_patterns(config: Config | None = None) -> tuple[str, ...]:
    """Return custom redaction patterns from *config*.

    Args:
        config: Optional :class:`Config`. If ``None``, returns empty tuple.
            Otherwise returns ``config.redaction.patterns``.
    """
    if config is None:
        return ()
    return config.redaction.patterns


def get_all_patterns(config: Config | None = None) -> list[str]:
    """Return all patterns (custom only; built-ins are always applied separately).

    This is a convenience to collect custom patterns for :func:`redact_text`.
    """
    return list(get_custom_patterns(config))

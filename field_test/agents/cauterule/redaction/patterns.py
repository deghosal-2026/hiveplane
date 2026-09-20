"""Built-in secret patterns for redaction."""

from __future__ import annotations

import re

# Each entry is (name, regex). Regex is compiled case-sensitive unless (?i) flag.
_BUILTIN_PATTERN_STRS: list[tuple[str, str]] = [
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("aws_secret_key", r"(?i)aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{40}"),
    ("github_token", r"ghp_[A-Za-z0-9]{36,}"),
    ("github_pat", r"github_pat_[A-Za-z0-9_]{82}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    ("openai_key", r"sk-[A-Za-z0-9]{20,}"),
    ("api_key", r"(?i)(api[_\s-]?(?:key|token|secret)|apikey)\s*[:=]\s*[A-Za-z0-9_\-]{8,}"),
    ("password", r"(?i)(password|passwd|pwd)\s*[:=]\s*[^\s\"']+"),
    ("secret_generic", r"(?i)secret\s*[:=]\s*[^\s\"']+"),
    ("bearer_token", r"Bearer\s+[A-Za-z0-9_\-\.]+"),
    (
        "private_key_block",
        r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    ),
    ("private_key", r"-----BEGIN (?:RSA )?PRIVATE KEY-----"),
    ("slack_token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("stripe_key", r"(?i)(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}"),
    (
        "high_entropy_assignment",
        r"(?i)(?:token|secret|key|password)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_-]{20,}['\"]?",
    ),
]


def _compile_patterns() -> list[re.Pattern[str]]:
    """Compile built-in patterns."""
    return [re.compile(p) for _, p in _BUILTIN_PATTERN_STRS]


BUILTIN_PATTERNS: list[re.Pattern[str]] = _compile_patterns()

BUILTIN_NAMES: list[str] = [name for name, _ in _BUILTIN_PATTERN_STRS]


def get_builtin_patterns() -> list[re.Pattern[str]]:
    """Return a copy of built-in compiled patterns."""
    return list(BUILTIN_PATTERNS)


def get_pattern_names() -> list[str]:
    """Return names of built-in patterns."""
    return list(BUILTIN_NAMES)

"""Auto-classified failure taxonomy."""

from __future__ import annotations


_TAXONOMY_KEYWORDS: dict[str, str] = {
    "git push": "git/push",
    "git pull": "git/pull",
    "git merge": "git/merge",
    "non-fast-forward": "git/push/non-fast-forward",
    "import": "python/import",
    "module not found": "python/import",
    "pip": "python/pip",
    "docker": "docker/network",
    "container": "docker/container",
    "network": "docker/network",
    "rejected": "git/push",
}


def classify_taxonomy(tool: str, error: str | None = None, output: str | None = None) -> str:
    """Classify a failure into a taxonomy label.

    Args:
        tool: Tool name.
        error: Error message if any.
        output: Tool output if any.

    Returns:
        Taxonomy string like ``git/push`` or ``python/import``.
        Defaults to ``unknown/error`` if no match.
    """
    haystack = " ".join(part for part in [tool, error or "", output or ""] if part).lower()
    # Prefer more specific matches (longer keys first).
    for keyword in sorted(_TAXONOMY_KEYWORDS, key=len, reverse=True):
        if keyword in haystack:
            return _TAXONOMY_KEYWORDS[keyword]
    # Fallback: use tool name as taxonomy.
    tool_clean = tool.strip().lower().replace(" ", "/") if tool.strip() else "unknown"
    return f"{tool_clean}/error" if error else f"{tool_clean}/success"

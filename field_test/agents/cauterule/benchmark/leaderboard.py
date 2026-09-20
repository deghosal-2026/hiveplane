"""Benchmark leaderboard — formats benchmark results as a markdown table."""

from __future__ import annotations

from typing import Any


class Leaderboard:
    """Formats benchmark results into a markdown leaderboard table.

    Each entry should be a dict with at minimum a ``"name"`` key and a
    ``"score"`` key. Extra fields are appended as additional columns.
    """

    def __init__(self, title: str = "Benchmark Leaderboard") -> None:
        self.title = title
        self._entries: list[dict[str, Any]] = []

    def add_entry(self, entry: dict[str, Any]) -> None:
        """Add a benchmark result entry.

        Args:
            entry: Dict with at least ``"name"`` and ``"score"`` keys.
        """
        self._entries.append(entry)

    def add_entries(self, entries: list[dict[str, Any]]) -> None:
        """Add multiple benchmark result entries.

        Args:
            entries: List of result dicts.
        """
        self._entries.extend(entries)

    def sort(self, key: str = "score", reverse: bool = True) -> None:
        """Sort entries in-place by a given key.

        Args:
            key: Field to sort by.
            reverse: Descending if True (default).
        """
        self._entries.sort(key=lambda e: e.get(key, 0), reverse=reverse)

    def render(self) -> str:
        """Render the leaderboard as a markdown table.

        Returns:
            A markdown-formatted string.
        """
        if not self._entries:
            return f"# {self.title}\n\n*No entries.*\n"

        headers = list(self._entries[0].keys())
        rows = [headers, ["---"] * len(headers)]
        for entry in self._entries:
            rows.append([str(entry.get(h, "")) for h in headers])

        col_widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]

        lines: list[str] = [f"# {self.title}\n"]
        for row in rows:
            cells = [cell.ljust(col_widths[i]) for i, cell in enumerate(row)]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines) + "\n"

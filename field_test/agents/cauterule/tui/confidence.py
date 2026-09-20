from __future__ import annotations

from textual.widgets import Static

from cauterule.models.rule import StandingRule


class ConfidenceCard(Static):
    DEFAULT_CSS = """
    ConfidenceCard {
        width: 40;
        height: 6;
        border: solid $surface;
        padding: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._rule: StandingRule | None = None

    def render_rule(self, rule: StandingRule) -> None:
        self._rule = rule
        lines = [
            f"Confidence: {rule.confidence:.2f}",
            f"Hit count: {rule.hit_count}",
            f"Last hit: {rule.last_match or 'never'}",
            f"Status: {rule.status}",
            f"Tags: {', '.join(rule.tags) if rule.tags else 'none'}",
        ]
        self.update("\n".join(lines))

    def render_dict(
        self,
        confidence: float,
        hit_count: int,
        last_match: str | None,
        status: str,
        tags: tuple[str, ...],
    ) -> None:
        lines = [
            f"Confidence: {confidence:.2f}",
            f"Hit count: {hit_count}",
            f"Last hit: {last_match or 'never'}",
            f"Status: {status}",
            f"Tags: {', '.join(tags) if tags else 'none'}",
        ]
        self.update("\n".join(lines))

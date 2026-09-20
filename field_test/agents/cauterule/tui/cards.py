from __future__ import annotations

from typing import Any

from textual.widgets import Static

from cauterule.models.rule import ReplayEvidence


class EvidenceCard(Static):
    DEFAULT_CSS = """
    EvidenceCard {
        width: 40;
        height: 6;
        border: solid $surface;
        padding: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._evidence: ReplayEvidence | None = None

    def render_evidence(self, evidence: ReplayEvidence) -> None:
        self._evidence = evidence
        prevented = len(evidence.failures_prevented)
        broken = len(evidence.successes_broken)
        self.update(
            f"Prevented {prevented} failures, "
            f"broke {broken} successes\n"
            f"Precision: {evidence.precision:.2f}\n"
            f"Recall: {evidence.recall:.2f}"
        )

    def render_dict(self, data: dict[str, Any]) -> None:
        prevented = len(data.get("failures_prevented", []))
        broken = len(data.get("successes_broken", []))
        precision = data.get("precision", 0.0)
        recall = data.get("recall", 0.0)
        self.update(
            f"Prevented {prevented} failures, "
            f"broke {broken} successes\n"
            f"Precision: {precision:.2f}\n"
            f"Recall: {recall:.2f}"
        )

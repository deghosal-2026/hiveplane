from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListView, RichLog

from cauterule.models.candidate import CandidateRule
from cauterule.promotion.executor import execute_promotion
from cauterule.store.manager import StoreManager
from cauterule.tui.annotate import AnnotationScreen
from cauterule.tui.cards import EvidenceCard
from cauterule.tui.confidence import ConfidenceCard
from cauterule.tui.filter import FilterWidget


class ReviewScreen(Screen[Any]):
    DEFAULT_CSS = """
    ReviewScreen {
        align: center top;
    }
    ReviewScreen > Vertical {
        width: 80;
        height: 100%;
        margin: 1;
    }
    ReviewScreen #queue {
        height: 12;
        border: solid $primary;
    }
    ReviewScreen #detail {
        height: 8;
        border: solid $secondary;
    }
    """

    def __init__(self, store: StoreManager | None = None) -> None:
        super().__init__()
        self._store = store or StoreManager()
        self._candidates: list[CandidateRule] = []
        self._current_index: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            FilterWidget(),
            Label("Candidate Queue", id="queue-label"),
            ListView(id="queue"),
            Label("Details", id="detail-label"),
            RichLog(id="detail", highlight=True),
            Horizontal(
                EvidenceCard(),
                ConfidenceCard(),
                id="cards",
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        self._populate_candidates()

    def _populate_candidates(self) -> None:
        rules = self._store.list_rules(status="active")
        self._candidates = []
        for r in rules:
            if not r.provenance or not r.provenance.source_trajectory:
                continue
            self._candidates.append(
                CandidateRule(
                    when=r.when,
                    do=r.do,
                    confidence=r.confidence,
                    reasoning=r.provenance.source_trajectory,
                    extraction_pass=r.provenance.extraction_pass,
                )
            )
        if self._candidates and self.is_mounted:
            self._show_candidate(self._candidates[0])

    def approve_current(self) -> None:
        if not self._candidates or self._current_index >= len(self._candidates):
            return
        self.app.push_screen(
            AnnotationScreen(self._candidates[self._current_index]), self._on_annotation
        )
        self._advance()

    def _on_annotation(self, annotation: dict[str, object] | None) -> None:
        if annotation is None:
            return
        candidate = self._candidates[self._current_index - 1]
        config = {
            "rules_dir": "rules",
            "source_trajectory": candidate.reasoning or "tui-review",
            "extracted_by": "tui-review",
            "extract_timestamp": "",
            "extraction_pass": candidate.extraction_pass,
            "promotion_mode": "human-review",
            "status": "active",
        }
        try:
            rule_id = execute_promotion(candidate, config)
            self._candidates.pop(self._current_index - 1)
            if self._current_index > 0:
                self._current_index -= 1
            detail = self.query_one("#detail", RichLog)
            detail.write(f"Promoted rule {rule_id}")
        except Exception as e:
            detail = self.query_one("#detail", RichLog)
            detail.write(f"Promotion failed: {e}")

    def reject_current(self) -> None:
        if not self._candidates or self._current_index >= len(self._candidates):
            return
        self._candidates.pop(self._current_index)
        if self._candidates:
            self._current_index = min(self._current_index, len(self._candidates) - 1)
            self._show_candidate(self._candidates[self._current_index])
        else:
            self._current_index = 0
            detail = self.query_one("#detail", RichLog)
            detail.clear()
            detail.write("No more candidates.")

    def _advance(self) -> None:
        self._current_index += 1
        if self._current_index < len(self._candidates):
            self._show_candidate(self._candidates[self._current_index])
        else:
            detail = self.query_one("#detail", RichLog)
            detail.clear()
            detail.write("No more candidates.")

    def _show_candidate(self, candidate: CandidateRule) -> None:
        detail = self.query_one("#detail", RichLog)
        detail.clear()
        detail.write(f"When: {candidate.when.trigger}")
        detail.write(f"Do: {candidate.do.directive}")
        detail.write(f"Confidence: {candidate.confidence}")
        if candidate.reasoning:
            detail.write(f"Reasoning: {candidate.reasoning}")

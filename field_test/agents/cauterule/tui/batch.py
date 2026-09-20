from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView

from cauterule.models.candidate import CandidateRule


class BatchReviewScreen(Screen[Any]):
    DEFAULT_CSS = """
    BatchReviewScreen {
        align: center top;
    }
    BatchReviewScreen > Vertical {
        width: 80;
        height: 100%;
        margin: 1;
    }
    BatchReviewScreen #batch-list {
        height: 20;
        border: solid $primary;
    }
    BatchReviewScreen #actions {
        height: 5;
        align: center middle;
    }
    BatchReviewScreen Button {
        margin: 1;
    }
    """

    def __init__(self, candidates: list[CandidateRule] | None = None) -> None:
        super().__init__()
        self._candidates = candidates or []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Label("Batch Review (10 candidates)", id="batch-label")
            yield ListView(
                *[
                    ListItem(Label(f"When: {c.when.trigger} | Do: {c.do.directive}"))
                    for c in self._candidates[:10]
                ],
                id="batch-list",
            )
            with Horizontal(id="actions"):
                yield Button("Approve All", id="approve-all", variant="primary")
                yield Button("Reject All", id="reject-all", variant="error")
                yield Button("Review Individually", id="review-individual", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "approve-all":
            self.dismiss({"action": "approve_all", "count": len(self._candidates[:10])})
        elif button_id == "reject-all":
            self.dismiss({"action": "reject_all", "count": len(self._candidates[:10])})
        elif button_id == "review-individual":
            self.dismiss({"action": "review_individual"})

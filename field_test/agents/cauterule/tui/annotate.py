from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, RadioButton, RadioSet

from cauterule.models.candidate import CandidateRule

FAILURE_CATEGORIES = (
    "logic_error",
    "missing_edge_case",
    "hallucination",
    "tool_misuse",
    "formatting",
    "other",
)


class AnnotationScreen(Screen[dict[str, object]]):
    DEFAULT_CSS = """
    AnnotationScreen {
        align: center middle;
    }
    AnnotationScreen > Vertical {
        width: 60;
        height: auto;
        margin: 1;
    }
    AnnotationScreen Label {
        margin-top: 1;
    }
    AnnotationScreen Button {
        margin-top: 1;
    }
    """

    def __init__(self, candidate: CandidateRule | None = None) -> None:
        super().__init__()
        self._candidate = candidate
        self._tags: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Label("Tags (comma-separated):")
            yield Input(placeholder="e.g. python,deployment,security", id="tags-input")
            yield Label("Comment:")
            yield Input(placeholder="Optional annotation comment", id="comment-input")
            yield Label("Failure category:")
            yield RadioSet(*[RadioButton(c) for c in FAILURE_CATEGORIES], id="category-select")
            yield Button("Submit Annotation", id="submit-annotation", variant="primary")
            yield Button("Cancel", id="cancel-annotation", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit-annotation":
            tags_input = self.query_one("#tags-input", Input)
            comment_input = self.query_one("#comment-input", Input)
            category_select = self.query_one("#category-select", RadioSet)
            raw_tags = tags_input.value.strip()
            self._tags = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else []
            self._comment = comment_input.value.strip()
            cat = category_select.pressed_button
            self._category = str(cat.label) if cat else ""
            self.dismiss(self._annotation_result())
        elif event.button.id == "cancel-annotation":
            self.dismiss(None)

    def _annotation_result(self) -> dict[str, object]:
        return {
            "tags": tuple(self._tags),
            "comment": getattr(self, "_comment", ""),
            "category": getattr(self, "_category", ""),
        }

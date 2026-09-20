from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding

from cauterule.tui.annotate import AnnotationScreen
from cauterule.tui.batch import BatchReviewScreen
from cauterule.tui.filter import FilterWidget
from cauterule.tui.review import ReviewScreen


class CauterRuleApp(App[None]):
    DEFAULT_CSS = """
    Screen {
        align: center middle;
    }
    """

    BINDINGS = [  # noqa: RUF012  # type: ignore[assignment]
        Binding("q", "quit", "Quit"),
        Binding("r", "push_review", "Review"),
        Binding("f", "toggle_filter", "Filter"),
        Binding("a", "approve", "Approve"),
        Binding("x", "reject", "Reject"),
    ]

    SCREENS = {  # noqa: RUF012  # type: ignore[assignment]
        "review": ReviewScreen,
        "batch": BatchReviewScreen,
        "annotate": AnnotationScreen,
    }

    def compose(self) -> ComposeResult:
        yield ReviewScreen()

    def action_push_review(self) -> None:
        self.push_screen("review")

    def action_toggle_filter(self) -> None:
        screen = self.screen
        if isinstance(screen, ReviewScreen | BatchReviewScreen):
            filter_widget = screen.query_one(FilterWidget)
            filter_widget.toggle()

    def action_approve(self) -> None:
        screen = self.screen
        if isinstance(screen, ReviewScreen):
            screen.approve_current()

    def action_reject(self) -> None:
        screen = self.screen
        if isinstance(screen, ReviewScreen):
            screen.reject_current()

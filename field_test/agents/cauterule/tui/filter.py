from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Label, Select, Static


class FilterWidget(Static):
    DEFAULT_CSS = """
    FilterWidget {
        width: 100%;
        height: auto;
        padding: 1;
        border: solid $surface;
        display: none;
    }
    FilterWidget.visible {
        display: block;
    }
    FilterWidget > Horizontal {
        height: auto;
        margin: 0 1;
    }
    FilterWidget Label {
        margin-right: 1;
        margin-top: 1;
    }
    FilterWidget Input {
        width: 20;
    }
    FilterWidget Select {
        width: 20;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._visible = False

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label("Tag:")
            yield Input(placeholder="filter by tag", id="filter-tag")
            yield Label("Status:")
            yield Select(
                [(s, s) for s in ["all", "active", "retired", "superseded"]],
                prompt="Status",
                id="filter-status",
            )
            yield Label("Min Confidence:")
            yield Input(placeholder="0.0", id="filter-min-conf")
            yield Label("Max Confidence:")
            yield Input(placeholder="1.0", id="filter-max-conf")

    def toggle(self) -> None:
        self._visible = not self._visible
        self.set_class(self._visible, "visible")

    def _read_tag(self) -> str:
        return self.query_one("#filter-tag", Input).value.strip().lower()

    def _read_status(self) -> str | None:
        val = self.query_one("#filter-status", Select).value
        return str(val) if val is not None else None

    def _read_min_conf(self) -> float:
        v = self.query_one("#filter-min-conf", Input).value.strip()
        return float(v) if v else 0.0

    def _read_max_conf(self) -> float:
        v = self.query_one("#filter-max-conf", Input).value.strip()
        return float(v) if v else 1.0

    def apply_filter(
        self,
        candidates: list[object],
        *,
        _tag: str | None = None,
        _status: str | None = None,
        _min_conf: float | None = None,
        _max_conf: float | None = None,
    ) -> list[object]:
        tag_val = _tag if _tag is not None else self._read_tag()
        status_val = _status if _status is not None else self._read_status()
        min_conf = _min_conf if _min_conf is not None else self._read_min_conf()
        max_conf = _max_conf if _max_conf is not None else self._read_max_conf()

        result: list[object] = []
        for c in candidates:
            tags: tuple[str, ...] = getattr(c, "tags", ())
            confidence: float = getattr(c, "confidence", 0.0)
            status: str = getattr(c, "status", "")

            if status_val is not None and status_val != "all" and status != status_val:
                continue

            if tag_val and not any(tag_val in t.lower() for t in tags):
                continue

            if not (min_conf <= confidence <= max_conf):
                continue

            result.append(c)

        return result

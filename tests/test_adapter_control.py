"""Tests for cooperative run control."""

from __future__ import annotations

import threading

import pytest

from hiveplane.adapters.errors import RunCancelledError
from hiveplane.adapters.worker import RunControl


def test_checkpoint_returns_when_running() -> None:
    RunControl().checkpoint()


def test_checkpoint_raises_when_cancelled() -> None:
    control = RunControl()
    control.cancel()
    assert control.cancelled is True
    with pytest.raises(RunCancelledError):
        control.checkpoint()


def test_checkpoint_blocks_while_paused_then_resumes() -> None:
    control = RunControl()
    control.pause()
    assert control.paused is True
    reached = threading.Event()

    def _worker() -> None:
        control.checkpoint()
        reached.set()

    thread = threading.Thread(target=_worker)
    thread.start()
    assert reached.wait(0.05) is False

    control.resume()
    assert control.paused is False
    assert reached.wait(1.0) is True
    thread.join()


def test_cancel_releases_a_paused_checkpoint() -> None:
    control = RunControl()
    control.pause()
    outcome: list[str] = []

    def _worker() -> None:
        try:
            control.checkpoint()
        except RunCancelledError:
            outcome.append("cancelled")

    thread = threading.Thread(target=_worker)
    thread.start()
    control.cancel()
    thread.join(1.0)
    assert outcome == ["cancelled"]

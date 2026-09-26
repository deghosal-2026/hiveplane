"""Taint marks and provenance propagation (M39-02)."""

from __future__ import annotations

from hiveplane.defense.taint import TaintRegistry, TaintTracker, TrustTag


def test_untrusted_value_blocks_destructive_tool() -> None:
    tracker = TaintTracker()
    tracker.mark(kind="tool", source_id="fetch_web")
    decision = tracker.gate_destructive(tool_id="shell_exec")
    assert decision.blocked is True
    assert "fetch_web" in decision.reason


def test_trusted_marks_do_not_block() -> None:
    tracker = TaintTracker()
    tracker.mark(kind="operator", source_id="task_input", tag=TrustTag.TRUSTED)
    decision = tracker.gate_destructive(tool_id="shell_exec")
    assert decision.blocked is False
    assert decision.sources == []


def test_allow_untrusted_flag_permits_destructive_tool() -> None:
    tracker = TaintTracker()
    tracker.mark(kind="tool", source_id="fetch_web")
    decision = tracker.gate_destructive(tool_id="shell_exec", allow_untrusted=True)
    assert decision.blocked is False


def test_derived_value_inherits_untrusted() -> None:
    tracker = TaintTracker()
    parent = tracker.mark(kind="tool", source_id="fetch_web")
    derived = tracker.mark_derived(
        kind="tool", source_id="summarize", derived_from=[parent.mark_id]
    )
    assert derived.tag is TrustTag.DERIVED
    assert tracker.gate_destructive(tool_id="shell_exec").blocked is True


def test_derived_from_trusted_is_trusted() -> None:
    tracker = TaintTracker()
    parent = tracker.mark(kind="operator", source_id="task_input", tag=TrustTag.TRUSTED)
    derived = tracker.mark_derived(
        kind="tool", source_id="summarize", derived_from=[parent.mark_id]
    )
    assert derived.tag is TrustTag.TRUSTED
    assert tracker.gate_destructive(tool_id="shell_exec").blocked is False


def test_decision_lists_untrusted_sources() -> None:
    tracker = TaintTracker()
    tracker.mark(kind="tool", source_id="fetch_web")
    tracker.mark(kind="trigger", source_id="webhook")
    decision = tracker.gate_destructive(tool_id="shell_exec")
    assert {source.source_id for source in decision.sources} == {"fetch_web", "webhook"}


def test_registry_scopes_tracker_per_run() -> None:
    registry = TaintRegistry()
    registry.for_run("run-1").mark(kind="tool", source_id="fetch_web")
    assert registry.for_run("run-1").gate_destructive(tool_id="shell_exec").blocked is True
    assert registry.for_run("run-2").gate_destructive(tool_id="shell_exec").blocked is False


def test_registry_clear_forgets_run() -> None:
    registry = TaintRegistry()
    registry.for_run("run-1").mark(kind="tool", source_id="fetch_web")
    registry.clear("run-1")
    assert registry.for_run("run-1").gate_destructive(tool_id="shell_exec").blocked is False

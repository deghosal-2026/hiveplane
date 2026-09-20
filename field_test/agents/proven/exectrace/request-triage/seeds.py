"""Seed builders and fixture loader for the request-triage demo agent.

========================================================
Why seeds exist
========================================================
Named scenarios produce deterministic inputs: the same seed always yields the
same category of run (normal / loop / high-cost), which makes tests, analytics,
and demo replays stable. Each builder maps directly to one scenario:

    * ``normal_request``     -> healthy, resolved run (few steps).
    * ``loop_request``       -> non-converging run that hits the step cap.
    * ``high_cost_request``  -> many steps -> high estimated cost.

``load_fixture`` reads the pre-computed run traces under ``examples/demo-agent/
fixtures`` (produced by running these scenarios) so analytics/tests can replay
traces without re-running the agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from request_triage.graph import DEFAULT_VERSION, TriageState

# Fixtures live two levels above this file: ``src/request_triage/seeds.py`` ->
# ``examples/demo-agent/fixtures``. ``resolve()`` anchors the path so it works
# regardless of the process's current working directory.
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"

# The canonical scenario set. The order is significant: analytics fixtures iterate
# it to generate one input per scenario.
SCENARIOS: tuple[str, str, str] = ("normal", "loop", "high_cost")


def normal_request(*, agent_version: str = DEFAULT_VERSION) -> TriageState:
    """Input for a healthy, resolved run.

    ``reset password`` is a real ``KNOWLEDGE_BASE`` key and ``acc_100`` is a known
    account, so this run converges: lookup succeeds, KB hit found, resolves.
    """
    return {
        "scenario": "normal",
        "intent": "reset password",
        "account_id": "acc_100",
        "agent_version": agent_version,
    }


def loop_request(*, agent_version: str = DEFAULT_VERSION) -> TriageState:
    """Input for a run that never converges (missing account -> repeated retries).

    ``acc_404`` is in ``MISSING_ACCOUNTS``, so every account lookup fails and the
    agent retries until the ``MAX_STEPS`` cap forces escalation.
    """
    return {
        "scenario": "loop",
        "intent": "missing_account",
        "account_id": "acc_404",
        "agent_version": agent_version,
    }


def high_cost_request(*, agent_version: str = DEFAULT_VERSION) -> TriageState:
    """Input for a run that burns many turns and inflates estimated cost.

    ``open_ended`` routes every planner turn to ``search_kb``; combined with a
    known account (``acc_200``) and the step cap, this maximizes tool calls and
    therefore estimated cost.
    """
    return {
        "scenario": "high_cost",
        "intent": "open_ended",
        "account_id": "acc_200",
        "agent_version": agent_version,
    }


def load_fixture(name: str) -> dict[str, Any]:
    """Load a named JSON fixture from ``examples/demo-agent/fixtures``.

    Validates the payload is a JSON object so callers get a clear error instead of
    an obscure ``AttributeError`` on malformed fixtures. ``encoding="utf-8"`` is
    explicit so fixture text is decoded deterministically on every platform.
    """
    path = FIXTURES_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"fixture {name!r} must be a JSON object, got {type(data).__name__}")
    return data


def all_requests(*, agent_version: str = DEFAULT_VERSION) -> dict[str, TriageState]:
    """Return one input per named scenario, keyed by scenario name.

    Convenience for tests and the analytics fixture generator: iterating this dict
    yields all three behavior classes in one go.
    """
    return {
        "normal": normal_request(agent_version=agent_version),
        "loop": loop_request(agent_version=agent_version),
        "high_cost": high_cost_request(agent_version=agent_version),
    }

"""Sample HivePlane plugin: a fan-out channel relay and a policy check (M56-06).

Copy this into your plugins directory and declare hooks pointing at
``sample_plugin:relay`` and ``sample_plugin:allow_all``.
"""

from __future__ import annotations

from typing import Any


def relay(target: str, message: dict[str, Any]) -> dict[str, Any]:
    """A fan-out channel hook that echoes where a message would go."""
    return {"relayed_to": target, "type": message.get("type")}


def allow_all(context: dict[str, Any]) -> dict[str, Any]:
    """A policy-check hook that allows everything (example only)."""
    return {"outcome": "allow", "reason": "sample plugin allows all"}

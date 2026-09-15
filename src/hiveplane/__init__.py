"""HivePlane — control plane for production agent fleets.

Register agents, certify them against a reproducible benchmark, admit only
certified agents to production, run them under budget/policy/sandbox, intervene
on live runs, deliver results, and observe the fleet.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]

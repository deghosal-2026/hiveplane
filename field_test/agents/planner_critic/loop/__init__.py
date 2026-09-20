"""Loop sub-package: the revise-until-approved controller and its helpers.

The controller :mod:`planner_critic.loop` orchestrates planner, deterministic
gates, critic, and the termination logic in the deterministic order defined
by the PRD pseudocode (§2.6.1). The deterministic helpers it composes live
in dedicated submodules:

* :mod:`planner_critic.loop.convergence` — circling-blockers / near-zero-diff
* :mod:`planner_critic.loop.regression` — the "new blocker" thrashing guard
* :mod:`planner_critic.loop.budget` — spend-budget enforcement
* :mod:`planner_critic.loop.ttl` — approval expiry (whole-plan drift, F-18)

Note: the WBS canonical layout names the controller file ``loop.py``, but a
``planner_critic/loop.py`` module cannot coexist with a ``planner_critic/loop/``
package (Python resolves the package first). We keep the package and the
controller as :mod:`planner_critic.loop` — see D13/DD-01.
"""

from __future__ import annotations

from ._controller import (
    CriticMode,
    LoopConfig,
    LoopResult,
    run_loop,
)

__all__ = ["CriticMode", "LoopConfig", "LoopResult", "run_loop"]

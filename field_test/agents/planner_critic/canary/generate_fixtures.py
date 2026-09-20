#!/usr/bin/env python3
"""Generate canary fixtures for all deterministic gates.

Each gate class gets a (good_plan, bad_plan) JSON pair in tests/canary/<gate>/.
Run this after schema changes to regenerate fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

CANARY_DIR = Path(__file__).resolve().parent


def _repo_root() -> Path:
    p = Path(__file__).resolve()
    for _ in range(10):
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    raise RuntimeError("cannot find repo root")


# Need to add the repo root to sys.path so we can import conftest
import sys

sys.path.insert(0, str(_repo_root()))


from conftest import make_plan, make_task

FIXTURES: dict[str, tuple[dict, dict]] = {}


def register(name: str, good: dict, bad: dict) -> None:
    FIXTURES[name] = (good, bad)


# -- schema_valid ----------------------------------------------------------
register(
    "schema_valid",
    make_plan(tasks=[make_task("t1")]).model_dump(mode="json"),
    make_plan(tasks=[]).model_dump(mode="json"),
)

# -- ordering ---------------------------------------------------------------
register(
    "ordering",
    make_plan(tasks=[make_task("t1", action="step1"), make_task("t2", action="step2")]).model_dump(mode="json"),
    make_plan(
        tasks=[make_task("t2", action="step2"), make_task("t1", action="step1")],
        dependencies=[("t2", "t1")],
    ).model_dump(mode="json"),
)

# -- dep_cycles -------------------------------------------------------------
register(
    "dep_cycles",
    make_plan(tasks=[make_task("t1"), make_task("t2")], dependencies=[("t1", "t2")]).model_dump(mode="json"),
    make_plan(
        tasks=[make_task("t1"), make_task("t2")],
        dependencies=[("t1", "t2"), ("t2", "t1")],
    ).model_dump(mode="json"),
)

# -- verification ------------------------------------------------------------
register(
    "verification",
    make_plan(
        tasks=[make_task("t1", verification={"method": "assert"})],
    ).model_dump(mode="json"),
    make_plan(tasks=[make_task("t1")]).model_dump(mode="json"),
)

# -- verification_ordering ---------------------------------------------------
register(
    "verification_ordering",
    make_plan(
        tasks=[
            make_task("verify", action="verify", target="t1"),
            make_task("t1", action="consume"),
        ],
    ).model_dump(mode="json"),
    make_plan(
        tasks=[
            make_task("t1", action="consume"),
            make_task("verify", action="verify", target="t1"),
        ],
    ).model_dump(mode="json"),
)

# -- rollback ----------------------------------------------------------------
register(
    "rollback",
    make_plan(tasks=[make_task("t1")]).model_dump(mode="json"),
    make_plan(
        tasks=[make_task("t1", action="dangerous", risk_class="high")],
    ).model_dump(mode="json"),
)

# -- rollback_credible -------------------------------------------------------
register(
    "rollback_credible",
    make_plan(
        tasks=[make_task("t1", risk_class="high", rollback={"method": "restore"})],
    ).model_dump(mode="json"),
    make_plan(
        tasks=[make_task("t1", risk_class="high", rollback={"method": "unreachable"})],
    ).model_dump(mode="json"),
)

# -- preconditions -----------------------------------------------------------
register(
    "preconditions",
    make_plan(
        tasks=[make_task("t1", target="resolved::resource")],
    ).model_dump(mode="json"),
    make_plan(
        tasks=[make_task("t1")],
    ).model_dump(mode="json"),
)

# -- parallel_safety ---------------------------------------------------------
register(
    "parallel_safety",
    make_plan(
        tasks=[make_task("t1", blast_radius="low"), make_task("t2", blast_radius="low")],
    ).model_dump(mode="json"),
    make_plan(
        tasks=[make_task("t1", blast_radius="high"), make_task("t2", blast_radius="high")],
    ).model_dump(mode="json"),
)

# -- requirement_trace -------------------------------------------------------
register(
    "requirement_trace",
    make_plan(tasks=[make_task("t1")]).model_dump(mode="json"),
    make_plan(tasks=[make_task("t1", target="untraced")]).model_dump(mode="json"),
)


def main() -> None:
    for name, (good, bad) in FIXTURES.items():
        gate_dir = CANARY_DIR / name
        gate_dir.mkdir(parents=True, exist_ok=True)
        (gate_dir / "good.json").write_text(json.dumps(good, indent=2, default=str))
        (gate_dir / "bad.json").write_text(json.dumps(bad, indent=2, default=str))
        print(f"  {name}: good.json + bad.json")


if __name__ == "__main__":
    main()
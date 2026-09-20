"""Coding-domain trajectory generator."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def generate_coding_trajectories(count: int = 10) -> list[Trajectory]:
    """Generate *count* coding-domain trajectories."""
    result: list[Trajectory] = []
    for i in range(count):
        success = i < count // 2
        ts = datetime.now(UTC).isoformat()
        task = (
            "Implement merge sort in Python"
            if success
            else f"Debug index-out-of-range in {['search', 'sort', 'filter'][i % 3]}.py"
        )
        steps = (
            Step(
                step_number=1,
                tool="read",
                input="def sort(arr): ...",
                output=None,
                error=None,
                state=None,
            ),
            Step(
                step_number=2,
                tool="edit",
                input="fix loop range",
                output="def sort(arr): ... arr.sort()",
                error="IndexError: list index out of range" if not success else None,
                state=None,
            ),
        )
        result.append(
            Trajectory(
                id=f"coding-{i:04d}",
                timestamp=ts,
                task=task,
                steps=steps,
                success=success,
                failure_point="step_2" if not success else None,
                failure_class="coding/index-error" if not success else None,
                quality_label="clear",
                domain="coding",
                severity="high" if not success else None,
                tags=("coding", "python"),
                agent_config=AgentConfig(model="gpt-4o", tools=("read", "edit")),
                environment=Environment(os="linux", ci=False),
                redacted=False,
            )
        )
    return result

"""Research-domain trajectory generator."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def generate_research_trajectories(count: int = 10) -> list[Trajectory]:
    """Generate *count* research-domain trajectories."""
    result: list[Trajectory] = []
    for i in range(count):
        success = i < count // 2
        ts = datetime.now(UTC).isoformat()
        task = (
            "Summarize recent transformer papers" if success else "Extract data from paywalled PDF"
        )
        steps = (
            Step(
                step_number=1,
                tool="web_fetch",
                input="https://arxiv.org/search?q=transformer",
                output="Paper titles found" if success else "403 Forbidden",
                error=None if success else "HTTP 403",
                state=None,
            ),
            Step(
                step_number=2,
                tool="read",
                input="downloaded paper",
                output=None,
                error=None,
                state=None,
            ),
        )
        result.append(
            Trajectory(
                id=f"research-{i:04d}",
                timestamp=ts,
                task=task,
                steps=steps,
                success=success,
                failure_point="step_1" if not success else None,
                failure_class="research/access-denied" if not success else None,
                quality_label="clear",
                domain="research",
                severity="medium" if not success else None,
                tags=("research", "arxiv"),
                agent_config=AgentConfig(model="gpt-4o", tools=("web_fetch", "read")),
                environment=Environment(os="macos", ci=False),
                redacted=False,
            )
        )
    return result

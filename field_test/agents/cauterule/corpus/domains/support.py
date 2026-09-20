"""Support-domain trajectory generator."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def generate_support_trajectories(count: int = 10) -> list[Trajectory]:
    """Generate *count* support-domain trajectories."""
    result: list[Trajectory] = []
    for i in range(count):
        success = i < count // 2
        ts = datetime.now(UTC).isoformat()
        task = "Triage customer login issue" if success else "Escalate unresolved billing ticket"
        steps = (
            Step(
                step_number=1,
                tool="grep",
                input="search logs for user_id=1234",
                output="Found 5 login attempts" if success else "No matching logs",
                error=None,
                state={"ticket": "TKT-001"},
            ),
            Step(
                step_number=2,
                tool="edit",
                input="update ticket status",
                output="Ticket resolved" if success else "Error: permissions insufficient",
                error=None if success else "PermissionDenied",
                state=None,
            ),
        )
        result.append(
            Trajectory(
                id=f"support-{i:04d}",
                timestamp=ts,
                task=task,
                steps=steps,
                success=success,
                failure_point="step_2" if not success else None,
                failure_class="support/permission-denied" if not success else None,
                quality_label="clear",
                domain="support",
                severity="low" if not success else None,
                tags=("support", "ticketing"),
                agent_config=AgentConfig(model="gpt-4o", tools=("grep", "edit")),
                environment=Environment(os="linux", ci=False),
                redacted=False,
            )
        )
    return result

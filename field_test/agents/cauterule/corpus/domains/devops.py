"""DevOps-domain trajectory generator."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def generate_devops_trajectories(count: int = 10) -> list[Trajectory]:
    """Generate *count* devops-domain trajectories."""
    result: list[Trajectory] = []
    for i in range(count):
        success = i < count // 2
        ts = datetime.now(UTC).isoformat()
        task = "Deploy microservice to k8s" if success else "Rollback failed deployment"
        steps = (
            Step(
                step_number=1,
                tool="bash",
                input="kubectl apply -f deploy.yaml",
                output="deployment.apps/svc created"
                if success
                else "Error: resource limit exceeded",
                error=None if success else "ResourceQuotaExceeded",
                state={"namespace": "prod"},
            ),
            Step(
                step_number=2,
                tool="bash",
                input="kubectl rollout status svc",
                output=None,
                error="timeout waiting for condition" if not success else "rolling update complete",
                state=None,
            ),
        )
        result.append(
            Trajectory(
                id=f"devops-{i:04d}",
                timestamp=ts,
                task=task,
                steps=steps,
                success=success,
                failure_point="step_2" if not success else None,
                failure_class="devops/deployment-timeout" if not success else None,
                quality_label="clear",
                domain="devops",
                severity="high" if not success else None,
                tags=("devops", "k8s"),
                agent_config=AgentConfig(model="claude-3.5", tools=("bash",)),
                environment=Environment(os="linux", ci=True),
                redacted=False,
            )
        )
    return result

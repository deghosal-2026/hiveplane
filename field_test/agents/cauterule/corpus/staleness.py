"""Staleness corpus — trajectories with outdated agent configs and environments."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def build_staleness_corpus() -> list[Trajectory]:
    """Build a corpus of staleness-test trajectories.

    These trajectories use outdated agent models, old tool lists, or
    deprecated environment contexts to test rule staleness detection.

    Returns:
        List of :class:`Trajectory` with stale metadata.
    """
    ts = datetime.now(UTC).isoformat()
    # Staleness 1: Very old model
    t1 = Trajectory(
        id="stale-model-001",
        timestamp=ts,
        task="Refactor legacy code with outdated model",
        steps=(
            Step(
                step_number=1,
                tool="read",
                input="def old_func(): pass",
                output="legacy code",
                error=None,
                state=None,
            ),
            Step(
                step_number=2,
                tool="edit",
                input="refactor to modern syntax",
                output="def new_func(): ...",
                error=None,
                state=None,
            ),
        ),
        success=True,
        quality_label="clear",
        domain="coding",
        severity=None,
        tags=("staleness", "deprecated-model"),
        agent_config=AgentConfig(model="gpt-3.5-turbo", tools=("read", "edit", "bash")),
        environment=Environment(os="ubuntu-18.04", ci=False),
        redacted=False,
    )
    # Staleness 2: Deprecated tool
    t2 = Trajectory(
        id="stale-tool-002",
        timestamp=ts,
        task="Deploy using legacy toolchain",
        steps=(
            Step(
                step_number=1,
                tool="bash",
                input="docker-compose up -d",
                output="Starting services...",
                error=None,
                state=None,
            ),
            Step(
                step_number=2,
                tool="bash",
                input="docker-compose ps",
                output="all services running",
                error=None,
                state=None,
            ),
        ),
        success=True,
        quality_label="clear",
        domain="devops",
        severity=None,
        tags=("staleness", "deprecated-tool"),
        agent_config=AgentConfig(model="claude-2", tools=("bash", "docker_compose")),
        environment=Environment(os="centos-7", ci=False),
        redacted=False,
    )
    # Staleness 3: CI environment that's been decommissioned
    t3 = Trajectory(
        id="stale-env-003",
        timestamp=ts,
        task="CI job on deprecated runner",
        steps=(
            Step(
                step_number=1,
                tool="bash",
                input="npm test",
                output="test suite failed",
                error="segfault",
                state=None,
            ),
            Step(
                step_number=2,
                tool="bash",
                input="npm run build",
                output="build failed",
                error="out of memory",
                state=None,
            ),
        ),
        success=False,
        failure_point="step_1",
        failure_class="ci/runner-deprecated",
        quality_label="misleading",
        domain="devops",
        severity="high",
        tags=("staleness", "ci-deprecated"),
        agent_config=AgentConfig(model="gpt-4o", tools=("bash",)),
        environment=Environment(os="windows-server-2016", ci=True),
        redacted=False,
    )
    return [t1, t2, t3]

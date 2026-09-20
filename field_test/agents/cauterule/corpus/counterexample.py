"""Counterexample corpus — trajectories where known rules should NOT fire."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def build_counterexample_corpus() -> list[Trajectory]:
    """Build a corpus of counterexample trajectories.

    These are trajectories that resemble failure patterns but succeed,
    testing that rules don't overfire on similar-but-different scenarios.

    Returns:
        List of :class:`Trajectory` counterexamples.
    """
    ts = datetime.now(UTC).isoformat()
    # Counterexample 1: Similar to a git-push failure but succeeds
    t1 = Trajectory(
        id="counterex-git-001",
        timestamp=ts,
        task="Push code to feature branch (expected failure that succeeds)",
        steps=(
            Step(
                step_number=1,
                tool="bash",
                input="git push origin feature/login",
                output="Everything up-to-date",
                error=None,
                state=None,
            ),
            Step(
                step_number=2,
                tool="bash",
                input="git push --force origin feature/login",
                output="remote: accepted\nTo origin",
                error=None,
                state=None,
            ),
        ),
        success=True,
        quality_label="clear",
        domain="coding",
        severity=None,
        tags=("counterexample", "git"),
        agent_config=AgentConfig(model="gpt-4o", tools=("bash",)),
        environment=Environment(os="linux", ci=True),
        redacted=False,
    )
    # Counterexample 2: Deployment succeeds despite previous failure pattern
    t2 = Trajectory(
        id="counterex-deploy-002",
        timestamp=ts,
        task="Deploy with resource limit check (previously failed, now succeeds)",
        steps=(
            Step(
                step_number=1,
                tool="bash",
                input="kubectl describe quota",
                output="ResourceQuota: 10 pods used / 10 limit → adjusted to 5",
                error=None,
                state={"namespace": "dev"},
            ),
            Step(
                step_number=2,
                tool="bash",
                input="kubectl apply -f reduced-deploy.yaml",
                output="deployment.apps/svc created",
                error=None,
                state=None,
            ),
        ),
        success=True,
        quality_label="clear",
        domain="devops",
        severity=None,
        tags=("counterexample", "deploy"),
        agent_config=AgentConfig(model="claude-3.5", tools=("bash",)),
        environment=Environment(os="linux", ci=True),
        redacted=False,
    )
    # Counterexample 3: Scrape succeeds despite paywall pattern
    t3 = Trajectory(
        id="counterex-scrape-003",
        timestamp=ts,
        task="Scrape open-access journal (previous paywall failure, now succeeds)",
        steps=(
            Step(
                step_number=1,
                tool="web_fetch",
                input="https://openaccess.example.org/paper",
                output="Full text retrieved",
                error=None,
                state={"method": "oa"},
            ),
            Step(
                step_number=2,
                tool="read",
                input="paper content",
                output="Extracted 3 sections",
                error=None,
                state=None,
            ),
        ),
        success=True,
        quality_label="clear",
        domain="research",
        severity=None,
        tags=("counterexample", "scrape"),
        agent_config=AgentConfig(model="gpt-4o", tools=("web_fetch", "read")),
        environment=Environment(os="macos", ci=False),
        redacted=False,
    )
    return [t1, t2, t3]

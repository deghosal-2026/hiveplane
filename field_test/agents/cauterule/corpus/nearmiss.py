"""Near-miss corpus — trajectories that closely resemble failures but diverge in critical details."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def build_nearmiss_corpus() -> list[Trajectory]:
    """Build a corpus of near-miss trajectories.

    These trajectories are *almost* failures — they contain the same tool calls
    and context as known failure patterns but succeed due to subtle differences.

    Returns:
        List of :class:`Trajectory` near-misses.
    """
    ts = datetime.now(UTC).isoformat()
    # Near-miss 1: Same git push error but retry succeeds
    t1 = Trajectory(
        id="nearmiss-git-001",
        timestamp=ts,
        task="Git push — first attempt fails but retry with correct branch succeeds",
        steps=(
            Step(
                step_number=1,
                tool="bash",
                input="git push origin main",
                output="! [rejected] non-fast-forward",
                error="failed to push",
                state=None,
            ),
            Step(
                step_number=2,
                tool="bash",
                input="git pull --rebase && git push origin main",
                output="Everything up-to-date\nTo origin\n* main",
                error=None,
                state=None,
            ),
        ),
        success=True,
        failure_point=None,
        failure_class=None,
        quality_label="ambiguous",
        domain="coding",
        severity="low",
        tags=("nearmiss", "git", "rebase"),
        agent_config=AgentConfig(model="gpt-4o", tools=("bash",)),
        environment=Environment(os="linux", ci=True),
        redacted=False,
    )
    # Near-miss 2: k8s deploy first pod fails but retry works
    t2 = Trajectory(
        id="nearmiss-k8s-002",
        timestamp=ts,
        task="K8s deploy — first pod OOM but second pod with limits succeeds",
        steps=(
            Step(
                step_number=1,
                tool="bash",
                input="kubectl apply -f deploy.yaml",
                output="pod/svc-1 created",
                error=None,
                state={"pod": "svc-1"},
            ),
            Step(
                step_number=2,
                tool="bash",
                input="kubectl get pods svc-1",
                output="OOMKilled",
                error="OOMKilled",
                state=None,
            ),
            Step(
                step_number=3,
                tool="edit",
                input="add memory limits to deploy.yaml",
                output="deploy.yaml updated",
                error=None,
                state=None,
            ),
            Step(
                step_number=4,
                tool="bash",
                input="kubectl apply -f deploy.yaml",
                output="pod/svc-2 created\nRunning",
                error=None,
                state=None,
            ),
        ),
        success=True,
        quality_label="multi-causal",
        domain="devops",
        severity="medium",
        tags=("nearmiss", "k8s", "oom"),
        agent_config=AgentConfig(model="claude-3.5", tools=("bash", "edit")),
        environment=Environment(os="linux", ci=True),
        redacted=False,
    )
    # Near-miss 3: Web scrape hits rate limit but waits and succeeds
    t3 = Trajectory(
        id="nearmiss-web-003",
        timestamp=ts,
        task="Web scrape — rate-limited first attempt, retry after delay succeeds",
        steps=(
            Step(
                step_number=1,
                tool="web_fetch",
                input="https://api.example.org/data",
                output=None,
                error="429 Too Many Requests",
                state={"retry_after": "5"},
            ),
            Step(step_number=2, tool="bash", input="sleep 5", output="", error=None, state=None),
            Step(
                step_number=3,
                tool="web_fetch",
                input="https://api.example.org/data",
                output="[{'id': 1, 'name': 'test'}]",
                error=None,
                state=None,
            ),
        ),
        success=True,
        quality_label="clear",
        domain="research",
        severity=None,
        tags=("nearmiss", "web", "rate-limit"),
        agent_config=AgentConfig(model="gpt-4o", tools=("web_fetch", "bash")),
        environment=Environment(os="linux", ci=False),
        redacted=False,
    )
    return [t1, t2, t3]

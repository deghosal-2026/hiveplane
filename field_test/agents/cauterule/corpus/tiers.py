"""Tiered corpus builder — generate balanced trajectory sets per tier."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory

_DOMAIN_POOL: list[str] = ["coding", "devops", "research", "support", "browser_automation"]

_TOOL_POOL: list[str] = [
    "bash",
    "read",
    "write",
    "edit",
    "grep",
    "glob",
    "web_fetch",
    "search",
]

_TASK_TEMPLATES_SUCCESS: list[str] = [
    "Implement {feature} in {lang}",
    "Deploy {service} to production",
    "Summarize {topic} from research papers",
    "Triage {issue} in ticketing system",
    "Fill out form for {purpose} on website",
]

_TASK_TEMPLATES_FAILURE: list[str] = [
    "Debug {issue} in {lang} codebase",
    "Configure {service} load balancer",
    "Extract data from {source} using web scraping",
    "Resolve {issue} reported by customer",
    "Navigate {site} to find {resource}",
]


def _make_trajectory(
    idx: int,
    success: bool,
    domain: str,
    tier: str,
) -> Trajectory:
    ts = datetime.now(UTC).isoformat()
    template_pool = _TASK_TEMPLATES_SUCCESS if success else _TASK_TEMPLATES_FAILURE
    template = random.choice(template_pool)
    task = template.format(
        feature=random.choice(["login", "auth", "cache", "logging", "search"]),
        lang=random.choice(["Python", "TypeScript", "Go", "Rust"]),
        service=random.choice(["nginx", "postgres", "redis", "k8s"]),
        topic=random.choice(["transformer models", "RAG", "fine-tuning", "embeddings"]),
        issue=random.choice(["regression", "OOM", "timeout", "perf"]),
        site=random.choice(["github.com", "docs.python.org", "pypi.org"]),
        resource=random.choice(["API docs", "release notes", "tutorial"]),
        purpose=random.choice(["onboarding", "report", "order"]),
        source=random.choice(["PDF reports", "HTML tables", "JSON APIs"]),
    )
    n_steps = random.randint(2, 6)
    steps: list[Step] = []
    for s in range(1, n_steps + 1):
        tool = random.choice(_TOOL_POOL)
        inp = f"step_{s}_input_{idx}" if random.random() > 0.3 else None
        out = f"step_{s}_output_{idx}" if random.random() > 0.3 else None
        err = None if success else (f"error_at_step_{s}" if s == n_steps else None)
        steps.append(
            Step(
                step_number=s,
                tool=tool,
                input=inp,
                output=out,
                error=err,
                state={"idx": idx, "tier": tier} if random.random() > 0.5 else None,
            )
        )
    failure_point = f"step_{n_steps}" if not success else None
    return Trajectory(
        id=f"{tier}-{domain}-{idx:06d}",
        timestamp=ts,
        task=task,
        steps=tuple(steps),
        success=success,
        failure_point=failure_point,
        failure_class=f"{domain}/generic" if not success else None,
        quality_label="clear",
        domain=domain,
        severity="medium" if not success else None,
        tags=(tier, domain),
        agent_config=AgentConfig(
            model=random.choice(["gpt-4o", "claude-3.5", None]),
            tools=("bash", "read"),
        ),
        environment=Environment(
            os=random.choice(["linux", "macos"]), ci=random.choice([True, False])
        ),
        redacted=False,
    )


def build_tiered_corpus(
    base_dir: str,
    tiers: dict[str, int],
    seed: int | None = None,
) -> dict[str, list[Trajectory]]:
    """Build a stratified corpus with balanced success/failure per tier.

    Each tier gets *count* trajectories (roughly half success, half failure),
    spread across available domains.

    Args:
        base_dir: Base path for corpus storage (unused in generation).
        tiers: Mapping of tier name to desired count.
        seed: Optional seed for deterministic generation.

    Returns:
        Dict mapping tier name to list of :class:`Trajectory`.
    """
    if seed is not None:
        random.seed(seed)

    result: dict[str, list[Trajectory]] = {}
    for tier_name, count in tiers.items():
        trajectories: list[Trajectory] = []
        half = count // 2
        for i in range(count):
            success = i < half
            domain = random.choice(_DOMAIN_POOL)
            trajectories.append(_make_trajectory(i, success, domain, tier_name))
        random.shuffle(trajectories)
        result[tier_name] = trajectories
    return result

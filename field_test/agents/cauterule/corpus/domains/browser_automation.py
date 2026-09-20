"""Browser-automation-domain trajectory generator."""

from __future__ import annotations

from datetime import UTC, datetime

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory


def generate_browser_automation_trajectories(count: int = 10) -> list[Trajectory]:
    """Generate *count* browser-automation-domain trajectories."""
    result: list[Trajectory] = []
    for i in range(count):
        success = i < count // 2
        ts = datetime.now(UTC).isoformat()
        task = "Fill out registration form" if success else "Scrape paginated search results"
        steps = (
            Step(
                step_number=1,
                tool="web_fetch",
                input="https://example.com/register",
                output="Page loaded" if success else "504 Gateway Timeout",
                error=None if success else "HTTP 504",
                state={"url": "https://example.com/register"},
            ),
            Step(
                step_number=2,
                tool="write",
                input="fill name, email, submit",
                output="Registration successful" if success else "CAPTCHA block",
                error=None if success else "CaptchaError",
                state=None,
            ),
        )
        result.append(
            Trajectory(
                id=f"browser-{i:04d}",
                timestamp=ts,
                task=task,
                steps=steps,
                success=success,
                failure_point="step_2" if not success else None,
                failure_class="browser/captcha" if not success else None,
                quality_label="clear",
                domain="browser_automation",
                severity="medium" if not success else None,
                tags=("browser", "form"),
                agent_config=AgentConfig(model="claude-3.5", tools=("web_fetch", "write")),
                environment=Environment(os="macos", ci=False),
                redacted=False,
            )
        )
    return result

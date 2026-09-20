"""Domain-specific trajectory generators."""

from cauterule.corpus.domains.browser_automation import generate_browser_automation_trajectories
from cauterule.corpus.domains.coding import generate_coding_trajectories
from cauterule.corpus.domains.devops import generate_devops_trajectories
from cauterule.corpus.domains.research import generate_research_trajectories
from cauterule.corpus.domains.support import generate_support_trajectories

__all__ = [
    "generate_browser_automation_trajectories",
    "generate_coding_trajectories",
    "generate_devops_trajectories",
    "generate_research_trajectories",
    "generate_support_trajectories",
]

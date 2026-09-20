from __future__ import annotations
import logging
import os
import re

from tools.github import GitHubClient
from tools.parser import parse_conventional_commit, detect_tag_convention
import tools.cache as cache
from state import AgentState, ConventionalCommit

logger = logging.getLogger(__name__)

REPO_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
README_CANDIDATES = ["README.md", "README", "readme.md", "README.rst", "README.markdown"]


def _get_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN")


def _validate_inputs(repo: str, base: str, head: str, gh: GitHubClient) -> list[str]:
    errors: list[str] = []
    if not REPO_PATTERN.match(repo):
        errors.append(f"Invalid repo format: '{repo}'. Expected 'owner/name'.")
        return errors
    for tag in (base, head):
        found = gh._request("GET", f"/repos/{repo}/git/ref/tags/{tag}")
        if found is None:
            errors.append(f"Tag '{tag}' not found in {repo}")
    return errors


def fetch_repo_data(state: AgentState) -> dict:
    repo = state["repo"]
    base = state["base_tag"]
    head = state["head_tag"]
    no_cache = state.get("no_cache", False)
    token = state.get("github_token") or _get_token()

    if state.get("commits"):
        logger.info("Commits already in state (%d), skipping fetch", len(state["commits"]))
        return {}

    with GitHubClient(token=token) as gh:
        errors = _validate_inputs(repo, base, head, gh)
        if errors:
            logger.error("Input validation failed: %s", errors)
            return {"errors": errors, "commits": [], "releases": [], "readme_content": "", "repo_tag_convention": ""}

        if not no_cache:
            cached_commits = cache.load_commits(repo, base, head)
            cached_releases = cache.load_releases(repo)
            cached_readme = cache.load_readme(repo)
            cached_convention = cache.load_tag_convention(repo)
        else:
            cached_commits = cached_releases = cached_readme = cached_convention = None

        cache_hit_count = 0

        commits_data: list[dict] = []
        if cached_commits is not None:
            commits_data = cached_commits
            cache_hit_count += 1
            logger.info("Cache hit: %d commits for %s %s...%s", len(commits_data), repo, base, head)
        else:
            commits_data = gh.fetch_commits(repo, base, head)
            if not commits_data:
                logger.warning("No commits found in range %s...%s. Tags may be adjacent or reversed.", base, head)
            cache.cache_commits(repo, base, head, commits_data)
            logger.info("Fetched %d commits from API for %s %s...%s", len(commits_data), repo, base, head)

        releases: list[dict] = []
        if cached_releases is not None:
            releases = cached_releases
            cache_hit_count += 1
            logger.info("Cache hit: %d releases for %s", len(releases), repo)
        else:
            releases = gh.fetch_releases(repo)
            cache.cache_releases(repo, releases)
            logger.info("Fetched %d releases from API for %s", len(releases), repo)

        readme_content = ""
        if cached_readme is not None:
            readme_content = cached_readme
            cache_hit_count += 1
            logger.info("Cache hit: readme for %s", repo)
        else:
            for candidate in README_CANDIDATES:
                readme_result = gh.fetch_file(repo, candidate)
                if readme_result is not None:
                    readme_content = readme_result
                    cache.cache_readme(repo, readme_content)
                    logger.info("Fetched %s for %s", candidate, repo)
                    break
            if not readme_content:
                logger.warning("No README found for %s (tried %s)", repo, README_CANDIDATES)

        tag_convention = ""
        if cached_convention is not None:
            tag_convention = cached_convention
            cache_hit_count += 1
            logger.info("Cache hit: tag convention '%s' for %s", tag_convention, repo)
        else:
            tags = gh.fetch_tags(repo)
            tag_convention = detect_tag_convention(tags)
            cache.cache_tag_convention(repo, tag_convention)
            logger.info("Detected tag convention '%s' for %s", tag_convention, repo)

        commits: list[ConventionalCommit] = []
        for c in commits_data:
            raw = c.get("commit", {}).get("message", "")
            parsed = parse_conventional_commit(raw)
            parsed["sha"] = c.get("sha", "")
            author_info = c.get("commit", {}).get("author", {})
            if author_info:
                parsed["author"] = author_info.get("name", "")
                parsed["date"] = author_info.get("date", "")
            commits.append(parsed)

    return {
        "commits": commits,
        "releases": releases,
        "readme_content": readme_content,
        "repo_tag_convention": tag_convention,
        "cache_hits": state.get("cache_hits", 0) + cache_hit_count,
    }


def fetch_releases_node(state: AgentState) -> dict:
    repo = state["repo"]
    token = os.environ.get("GITHUB_TOKEN")

    with GitHubClient(token=token) as gh:
        releases = gh.fetch_releases(repo, limit=10)
    return {"releases": releases}

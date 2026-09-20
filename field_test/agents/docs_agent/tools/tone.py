from __future__ import annotations
import logging

from tools.github import GitHubClient
from state import ToneExample

logger = logging.getLogger(__name__)


class ToneRetriever:
    README_CANDIDATES = ["README.md", "README", "readme.md", "README.rst", "README.markdown"]

    def __init__(self, github: GitHubClient):
        self.github = github

    def fetch_release_notes(self, repo: str, limit: int = 3) -> list[ToneExample]:
        releases = self.github.fetch_releases(repo, limit=limit)
        examples: list[ToneExample] = []
        for r in releases:
            body = r.get("body", "") or ""
            tag = r.get("tag_name", "") or ""
            if body.strip():
                examples.append(ToneExample(source=f"release/{tag}", text=body.strip()))
        logger.info("Fetched %d release note examples for %s", len(examples), repo)
        return examples

    def fetch_readme(self, repo: str) -> ToneExample | None:
        for candidate in self.README_CANDIDATES:
            content = self.github.fetch_file(repo, candidate)
            if content:
                return ToneExample(source="readme", text=content.strip())
        return None

    def fetch_contributing(self, repo: str) -> ToneExample | None:
        for candidate in ("CONTRIBUTING.md", "CONTRIBUTING", "contributing.md"):
            content = self.github.fetch_file(repo, candidate)
            if content:
                return ToneExample(source="contributing", text=content.strip())
        return None

    def get_tone_examples(self, repo: str, releases: list[dict] | None = None, readme_content: str | None = None) -> list[ToneExample]:
        if releases is not None:
            examples: list[ToneExample] = []
            for r in releases:
                body = r.get("body", "") or ""
                tag = r.get("tag_name", "") or ""
                if body.strip():
                    examples.append(ToneExample(source=f"release/{tag}", text=body.strip()))
        else:
            examples = self.fetch_release_notes(repo)

        if readme_content is not None and readme_content.strip():
            examples.append(ToneExample(source="readme", text=readme_content.strip()))
        else:
            readme = self.fetch_readme(repo)
            if readme:
                examples.append(readme)

        contributing = self.fetch_contributing(repo)
        if contributing:
            examples.append(contributing)
        logger.info("Total tone examples for %s: %d", repo, len(examples))
        return examples

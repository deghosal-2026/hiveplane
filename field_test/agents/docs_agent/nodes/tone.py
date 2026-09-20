from __future__ import annotations
import logging
import os

from tools.github import GitHubClient
from tools.tone import ToneRetriever
from state import AgentState

logger = logging.getLogger(__name__)


def retrieve_tone(state: AgentState) -> dict:
    repo = state["repo"]
    releases = state.get("releases", [])
    readme_content = state.get("readme_content", "")

    import sys
    print(f"  Retrieving tone examples...", file=sys.stderr, flush=True)

    if releases:
        examples: list = []
        for r in releases:
            body = r.get("body", "") or ""
            tag = r.get("tag_name", "") or ""
            if body.strip():
                examples.append({"source": f"release/{tag}", "text": body.strip()})
        if readme_content and readme_content.strip():
            examples.append({"source": "readme", "text": readme_content.strip()})
        print(f"  Got {len(examples)} tone examples (from cache)", file=sys.stderr, flush=True)
        return {"tone_examples": examples}

    token = state.get("github_token") or os.environ.get("GITHUB_TOKEN")
    with GitHubClient(token=token) as gh:
        retriever = ToneRetriever(gh)
        examples = retriever.get_tone_examples(repo, releases=releases, readme_content=readme_content)

    print(f"  Got {len(examples)} tone examples (from API)", file=sys.stderr, flush=True)
    logger.info("Retrieved %d tone examples for %s", len(examples), repo)
    return {"tone_examples": examples}

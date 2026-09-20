import time
import logging
import base64
from typing import Any
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"
MAX_403_RETRIES = 4
MAX_5XX_RETRIES = 3
RETRY_DELAY_5XX = 2.0
INITIAL_BACKOFF = 1.0


def _parse_next_link(link_header: str) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split("<")[1].split(">")[0]
    return None


class GitHubClient:
    def __init__(self, token: str | None = None):
        self.token = token
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers=self._headers(),
            timeout=30.0,
            follow_redirects=True,
        )
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset: int | None = None

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "release-narrator/1.0",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _request(self, method: str, path: str, **kwargs) -> Any:
        for attempt in range(MAX_403_RETRIES + 1):
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.RequestError as e:
                if attempt < MAX_403_RETRIES:
                    wait = INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning("Request error (attempt %d/%d): %s. Retrying in %.1fs", attempt + 1, MAX_403_RETRIES, e, wait)
                    time.sleep(wait)
                    continue
                raise

            self._track_rate_limit(resp)

            if resp.status_code == 403:
                remaining = resp.headers.get("X-RateLimit-Remaining")
                retry_after = resp.headers.get("Retry-After")
                if remaining == "0":
                    if attempt < MAX_403_RETRIES:
                        wait = INITIAL_BACKOFF * (2 ** attempt)
                        logger.warning("Rate limited (attempt %d/%d). Retrying in %.1fs", attempt + 1, MAX_403_RETRIES, wait)
                        time.sleep(wait)
                        continue
                    reset = resp.headers.get("X-RateLimit-Reset", "unknown")
                    raise RuntimeError(f"GitHub API rate limited. Resets at {reset}. Path: {path}")
                elif retry_after:
                    wait = int(retry_after)
                    logger.warning("Secondary rate limit. Retrying in %ds", wait)
                    time.sleep(wait)
                    continue
                else:
                    raise RuntimeError(f"GitHub API 403 Forbidden. Token may lack access to {path}.")

            if resp.status_code == 404:
                logger.error("404 not found: %s", path)
                return None

            if resp.status_code >= 500:
                if attempt < MAX_5XX_RETRIES:
                    logger.warning("5xx error (attempt %d/%d): %d. Retrying in %.1fs", attempt + 1, MAX_5XX_RETRIES, resp.status_code, RETRY_DELAY_5XX)
                    time.sleep(RETRY_DELAY_5XX)
                    continue
                raise RuntimeError(f"GitHub API unreachable. Try again later. Path: {path}")

            resp.raise_for_status()
            return resp.json()

        return None

    def _track_rate_limit(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if remaining is not None:
            self.rate_limit_remaining = int(remaining)
        if reset is not None:
            self.rate_limit_reset = int(reset)

    def _paginate(self, path: str, params: dict | None = None) -> list[dict]:
        items: list[dict] = []
        url: str | None = path
        while url:
            resp = self._client.request("GET", url, params=params)
            self._track_rate_limit(resp)
            if resp.status_code == 404:
                return items
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                items.extend(data)
            next_url = _parse_next_link(resp.headers.get("Link", ""))
            url = next_url
            params = None
        return items

    def fetch_commits(self, repo: str, base: str, head: str) -> list[dict]:
        all_commits: list[dict] = []
        page = 1
        per_page = 250
        while True:
            data = self._request(
                "GET",
                f"/repos/{repo}/compare/{base}...{head}",
                params={"per_page": per_page, "page": page},
            )
            if data is None:
                break
            commits = data.get("commits", [])
            all_commits.extend(commits)
            total = data.get("total_commits", len(commits))
            if len(all_commits) >= total or not commits:
                break
            page += 1
        return all_commits

    def fetch_merged_prs(self, repo: str, since: str) -> list[dict]:
        merged: list[dict] = []
        page = 1
        per_page = 100
        while True:
            data = self._request(
                "GET",
                f"/repos/{repo}/pulls",
                params={
                    "state": "closed", "sort": "updated", "direction": "desc",
                    "per_page": per_page, "page": page,
                },
            )
            if not data:
                break
            if all(pr.get("updated_at", "") < since for pr in data):
                break
            for pr in data:
                merged_at = pr.get("merged_at")
                if merged_at and merged_at >= since:
                    merged.append(pr)
            if len(data) < per_page:
                break
            page += 1
        return merged

    def fetch_releases(self, repo: str, limit: int = 10) -> list[dict]:
        per_page = min(limit, 100)
        data = self._request("GET", f"/repos/{repo}/releases", params={"per_page": per_page})
        return data if isinstance(data, list) else []

    def fetch_tags(self, repo: str) -> list[dict]:
        return self._paginate(f"/repos/{repo}/tags", params={"per_page": 100})

    def fetch_file(self, repo: str, path: str) -> str | None:
        data = self._request("GET", f"/repos/{repo}/contents/{path}")
        if data is None:
            return None
        content = data.get("content")
        if content is None:
            return None
        return base64.b64decode(content).decode("utf-8")

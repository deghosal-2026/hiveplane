"""GitHub REST API client for PR diff fetching, comments, labels, and check runs."""

import base64
import time

import httpx


class GitHubClient:
    def __init__(self, token: str, repo: str):
        self.base_url = f"https://api.github.com/repos/{repo}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        self._client = httpx.Client(headers=self.headers, follow_redirects=True)

    def get_pr_diff(self, pr_number: int) -> str:
        headers = {**self.headers, "Accept": "application/vnd.github.v3.diff"}
        resp = self._request("GET", f"pulls/{pr_number}", headers=headers)
        return resp.text

    def get_commit_diff(self, commit_sha: str) -> str:
        headers = {**self.headers, "Accept": "application/vnd.github.v3.diff"}
        resp = self._request("GET", f"commits/{commit_sha}", headers=headers)
        return resp.text

    def get_file_content(self, path: str, ref: str = "HEAD") -> str | None:
        url = f"{self.base_url}/contents/{path}"
        resp = self._client.request("GET", url, params={"ref": ref}, headers=self.headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return base64.b64decode(data["content"]).decode()

    def post_comment(self, pr_number: int, body: str) -> int:
        resp = self._request("POST", f"issues/{pr_number}/comments", json={"body": body})
        return resp.json()["id"]

    def add_label(self, pr_number: int, label: str) -> None:
        self._request("POST", f"issues/{pr_number}/labels", json={"labels": [label]})

    def remove_label(self, pr_number: int, label: str) -> None:
        self._request("DELETE", f"issues/{pr_number}/labels/{label}")

    def create_check_run(self, name: str, conclusion: str, output: dict) -> dict:
        resp = self._request(
            "POST",
            "check-runs",
            json={
                "name": name,
                "conclusion": conclusion,
                "output": output,
                "status": "completed",
            },
        )
        return resp.json()

    def update_check_run(self, check_run_id: int, conclusion: str, output: dict) -> None:
        self._request(
            "PATCH",
            f"check-runs/{check_run_id}",
            json={"conclusion": conclusion, "output": output},
        )

    def get_reactions(self, comment_id: int) -> list[dict]:
        resp = self._request("GET", f"issues/comments/{comment_id}/reactions")
        return resp.json()

    def post_reaction(self, comment_id: int, content: str) -> None:
        self._request(
            "POST",
            f"issues/comments/{comment_id}/reactions",
            json={"content": content},
        )

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}/{path}"
        max_retries = 3
        resp: httpx.Response | None = None
        for attempt in range(max_retries):
            resp = self._client.request(method, url, **kwargs)
            if resp.status_code == 429 and attempt < max_retries - 1:
                retry_after = int(resp.headers.get("Retry-After", 5))
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500 and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp
        if resp is not None:
            resp.raise_for_status()
        return resp  # type: ignore[return-value]

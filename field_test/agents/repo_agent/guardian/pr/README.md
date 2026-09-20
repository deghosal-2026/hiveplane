# pr — GitHub Pull Request Integration

Interfaces with the GitHub API to post results on PRs.

- `client.py` — GitHub REST API client (httpx), diff fetching, comment/label/check run management
- `commentator.py` — Generates the markdown PR comment with collapsible violation sections
- `labeler.py` — Maps risk level to guardian:* labels
- `check_run.py` — Generates Check Run summary for GitHub Checks tab

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")


def _sanitize(name: str) -> str:
    return name.replace("/", "-").replace("\\", "-")


def _repo_dir(repo: str) -> Path:
    safe = repo.replace("/", "-")
    return DATA_DIR / safe


def _commits_dir(repo: str) -> Path:
    return _repo_dir(repo) / "commits"


def cache_commits(repo: str, base: str, head: str, data: list[dict]) -> None:
    d = _commits_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    safe_name = f"{_sanitize(base)}...{_sanitize(head)}.json"
    path = d / safe_name
    _write_json(path, data)


def load_commits(repo: str, base: str, head: str) -> list[dict] | None:
    safe_name = f"{_sanitize(base)}...{_sanitize(head)}.json"
    path = _commits_dir(repo) / safe_name
    return _read_json(path)


def cache_releases(repo: str, data: list[dict]) -> None:
    d = _repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "releases.json", data)


def load_releases(repo: str) -> list[dict] | None:
    return _read_json(_repo_dir(repo) / "releases.json")


def cache_tags(repo: str, data: list[dict]) -> None:
    d = _repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "tags.json", data)


def load_tags(repo: str) -> list[dict] | None:
    return _read_json(_repo_dir(repo) / "tags.json")


def cache_readme(repo: str, content: str) -> None:
    d = _repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "readme.md"
    path.write_text(content, encoding="utf-8")


def load_readme(repo: str) -> str | None:
    path = _repo_dir(repo) / "readme.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def cache_tag_convention(repo: str, convention: str) -> None:
    d = _repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    _write_json(d / "tag_convention.json", {"convention": convention})


def load_tag_convention(repo: str) -> str | None:
    data = _read_json(_repo_dir(repo) / "tag_convention.json")
    if data is None:
        return None
    return data.get("convention")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Corrupt cache file %s: %s. Treating as cache miss.", path, e)
        return None

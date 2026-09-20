from __future__ import annotations
import os
import yaml
from typing import Any


SEARCH_PATHS = [
    "config.yaml",
    os.path.expanduser("~/.config/release-narrator/config.yaml"),
]


def _find_config() -> str | None:
    for path in SEARCH_PATHS:
        if os.path.exists(path):
            return path
    return None


def load_config(path: str | None = None) -> dict[str, Any]:
    if path is None:
        path = _find_config()
    if path is None:
        raise FileNotFoundError(
            "No config.yaml found. Create one from config.yaml.example:\n"
            "  cp config.yaml.example config.yaml\n"
            "Then fill in github.token."
        )
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    github = cfg.get("github", {})
    token = github.get("token") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            f"Missing required config field: 'github.token'. "
            f"Set it in {path} or export GITHUB_TOKEN=..."
        )

    llm_cfg = cfg.get("llm", {}).get("local", {})
    base_url = llm_cfg.get("base_url", "http://127.0.0.1:8000/v1")
    llm_api_key = llm_cfg.get("api_key", "omlx-test")
    llm_model = llm_cfg.get("model", "gemma-3-12b-it-qat-4bit")

    return {
        "github_token": token,
        "llm_base_url": base_url,
        "llm_api_key": llm_api_key,
        "llm_model": llm_model,
        "output_dir": cfg.get("output_dir", "output"),
        "data_dir": cfg.get("data_dir", "data"),
    }
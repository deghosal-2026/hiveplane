"""Single-rule sharing via GitHub gist — ``cauterule share`` (#547).

Posts a rule plus a ``PROVENANCE.json`` envelope as a (secret-by-default)
gist; ``pack install <gist-url>`` imports it back.
"""

from __future__ import annotations

import difflib
import json
import os
import urllib.request
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from cauterule import __version__
from cauterule.export.redaction import contains_secret, redact_export
from cauterule.models.rule import StandingRule
from cauterule.store.manager import StoreManager

FORMAT = "cauterule-share/1"


class GistApi(Protocol):
    """Gist surface (real or fake)."""

    def create_gist(self, description: str, public: bool, files: dict[str, str]) -> str:
        """Create a gist. Returns the gist URL."""
        ...

    def fetch_gist(self, gist_id: str) -> dict[str, str]:
        """Return {filename: content} for a gist."""
        ...


def _token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "no GitHub token: set GH_TOKEN (or run `gh auth login` to seed it) before sharing"
        )
    return token


class GitHubGistApi:
    """Live GitHub Gist API via urllib."""

    _API = "https://api.github.com"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"{self._API}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "User-Agent": "cauterule",
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {_token()}",
            },
            method=method,
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def create_gist(self, description: str, public: bool, files: dict[str, str]) -> str:
        """Create a gist. Returns the gist URL."""
        result = self._request(
            "POST",
            "/gists",
            {
                "description": description,
                "public": public,
                "files": {name: {"content": content} for name, content in files.items()},
            },
        )
        return str(result.get("html_url", ""))

    def fetch_gist(self, gist_id: str) -> dict[str, str]:
        """Return {filename: content} for a gist."""
        result = self._request("GET", f"/gists/{gist_id}")
        files = result.get("files", {}) if isinstance(result, dict) else {}
        out = {}
        for name, meta in files.items():
            content = meta.get("content") if isinstance(meta, dict) else None
            if content is None and isinstance(meta, dict) and meta.get("raw_url"):
                raw_request = urllib.request.Request(
                    str(meta["raw_url"]), headers={"User-Agent": "cauterule"}
                )
                with urllib.request.urlopen(raw_request) as response:
                    content = response.read().decode("utf-8")
            out[str(name)] = str(content or "")
        return out


def share_rule(
    rule_id: str,
    store: str = "rules",
    public: bool = False,
    description: str = "",
    include_provenance: bool = True,
    open_browser: bool = False,
    filename: str | None = None,
    assume_yes: bool = False,
    api: GistApi | None = None,
) -> dict[str, Any]:
    """Share a rule as a GitHub gist. Returns {url, files, redacted}."""
    manager = StoreManager(base_dir=store)
    rule = manager.get_rule(rule_id)
    if rule is None:
        known = [r.id for r in manager.list_rules()]
        hint = difflib.get_close_matches(rule_id, known, n=3)
        suffix = f" — did you mean {hint}?" if hint else ""
        raise ValueError(f"rule {rule_id!r} not found in store{suffix}")

    rule_path = Path(store) / f"{rule.id}.yaml"
    payload = (
        rule_path.read_text(encoding="utf-8")
        if rule_path.is_file()
        else yaml.safe_dump(rule.to_dict(), sort_keys=False)
    )
    if contains_secret(payload):
        redacted_payload = redact_export(payload)
        if contains_secret(redacted_payload):
            raise ValueError(
                f"rule {rule.id} contains unredactable secrets; refusing to share "
                "(remove the secret fields first)"
            )
        redacted = True
        payload = redacted_payload
    else:
        redacted = False

    gist_name = filename or f"{rule.id}.yaml"
    files = {gist_name: payload}
    envelope: dict[str, Any] = {
        "format": FORMAT,
        "rule_id": rule.id,
        "shared_by": os.environ.get("USER", "cauterule"),
        "shared_at": datetime.now(UTC).isoformat(),
        "cauterule_version": __version__,
        "source_pack": rule.pack,
        "cert": {"passed": True},
        "redacted": redacted,
        "upstream": None,
    }
    if include_provenance:
        envelope["provenance"] = rule.provenance.to_dict()
        files["PROVENANCE.json"] = json.dumps(envelope, indent=2)
    author = rule.provenance.extracted_by
    if public and not assume_yes and author and "@" in author:
        raise ValueError(
            "public gist would expose an author email in provenance; "
            "re-run with --yes to confirm or drop --public"
        )

    gist_api = api or GitHubGistApi()
    url = gist_api.create_gist(
        description or f"Cauterule {rule.id} — {rule.when.trigger[:80]}",
        public,
        files,
    )
    if open_browser:
        webbrowser.open(url)
    return {"url": url, "files": sorted(files), "redacted": redacted, "rule_id": rule.id}


def fetch_gist_rule(
    gist_id: str, api: GistApi | None = None
) -> tuple[StandingRule, dict[str, str]]:
    """Fetch and parse the single rule in a gist without importing it (#799)."""
    gist_api = api or GitHubGistApi()
    files = gist_api.fetch_gist(gist_id)
    rule_files = {n: c for n, c in files.items() if n.startswith("R-") and n.endswith(".yaml")}
    if not rule_files:
        raise ValueError(f"gist {gist_id} contains no R-*.yaml rule file")
    if len(rule_files) > 1:
        raise ValueError(
            f"gist {gist_id} contains several rule files {sorted(rule_files)}; expected exactly one"
        )
    gist_name, content = next(iter(rule_files.items()))
    data = yaml.safe_load(content)
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError(f"gist {gist_id}: {gist_name} is not a valid rule (missing id)")
    return StandingRule.from_dict(data), files


def import_gist(
    gist_id: str,
    store: str = "rules",
    as_id: str | None = None,
    force: bool = False,
    api: GistApi | None = None,
) -> dict[str, Any]:
    """Import a shared rule gist into the store. Returns a summary dict."""
    rule, files = fetch_gist_rule(gist_id, api)
    target_id = as_id or rule.id
    manager = StoreManager(base_dir=store)
    existing = manager.get_rule(target_id)
    if existing is not None and not force:
        raise ValueError(
            f"rule {target_id} already exists in the store; "
            "re-run with --force to overwrite (default aborts)"
        )
    provenance_summary: dict[str, Any] = {}
    if "PROVENANCE.json" in files:
        try:
            envelope = json.loads(files["PROVENANCE.json"])
            provenance_summary = {
                "shared_by": envelope.get("shared_by"),
                "shared_at": envelope.get("shared_at"),
                "cauterule_version": envelope.get("cauterule_version"),
            }
        except (json.JSONDecodeError, AttributeError):
            provenance_summary = {}

    import dataclasses

    tags = (*tuple(rule.tags), "imported-from-gist")
    imported = dataclasses.replace(rule, id=target_id, tags=tuple(dict.fromkeys(tags)))
    manager.add_rule(imported)
    return {
        "id": target_id,
        "gist": gist_id,
        "tags": list(imported.tags),
        "provenance": provenance_summary,
    }

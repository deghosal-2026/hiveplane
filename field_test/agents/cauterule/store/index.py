"""Index file manager — maintains ``rules/index.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cauterule.models.rule import StandingRule


class IndexManager:
    """Read/write the rule-store index file.

    The index is a YAML mapping with a single ``rules`` key containing a
    list of rule entries.  Each entry captures id, status, summary, tags,
    taxonomy, hit_count, last_match, and pack.
    """

    def __init__(self, base_dir: str = "rules") -> None:
        """IndexManager(*base_dir*).

        Args:
            base_dir: Directory that contains ``index.yaml``.
        """
        self.base_dir = Path(base_dir)

    @property
    def _index_path(self) -> Path:
        return self.base_dir / "index.yaml"

    def _ensure_dir(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def load_index(self) -> dict[str, Any]:
        """Return the full index dict.

        Returns:
            The parsed YAML contents (a dict), or an empty dict if the
            index file does not exist.

        Raises:
            ValueError: If the index exists but is not a mapping with a
                ``rules`` key (#523).
        """
        path = self._index_path
        if not path.is_file():
            return {"rules": []}
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, dict) and "rules" in data and isinstance(data["rules"], list):
            return data
        msg = f"index.yaml must be a mapping with a 'rules' list, got {type(data).__name__}"
        raise ValueError(msg)

    def save_index(self, entries: dict[str, Any]) -> None:
        """Write *entries* to ``index.yaml``.

        Args:
            entries: A serializable dict with a ``rules`` key.
        """
        self._ensure_dir()
        with self._index_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(entries, fh, sort_keys=False, allow_unicode=True)

    def add_entry(self, rule: StandingRule) -> None:
        """Append or replace an index entry for *rule* (upsert, #523).

        Args:
            rule: The rule whose metadata to index.
        """
        self.update_entry(rule)

    def remove_entry(self, rule_id: str) -> None:
        """Remove the index entry for *rule_id*.

        Non-dict entries are skipped defensively so a partially-corrupt
        index does not abort the mutation.

        Args:
            rule_id: Id of the entry to remove.
        """
        index = self.load_index()
        rules_list: list[Any] = index.get("rules", [])
        index["rules"] = [
            e for e in rules_list if not isinstance(e, dict) or e.get("id") != rule_id
        ]
        self.save_index(index)

    def update_entry(self, rule: StandingRule) -> None:
        """Refresh the index entry for *rule*.

        Non-dict entries are skipped defensively so a partially-corrupt
        index does not abort the mutation (#523, code-review).

        Args:
            rule: The rule whose metadata to update in the index.
        """
        index = self.load_index()
        rules_list: list[Any] = index.get("rules", [])
        entry: dict[str, Any] = {
            "id": rule.id,
            "status": rule.status,
            "summary": f"when={rule.when.trigger!r} do={rule.do.directive!r}",
            "tags": list(rule.tags) if rule.tags else [],
            "hit_count": rule.hit_count,
            "last_match": rule.last_match,
            "pack": rule.pack,
        }
        if rule.taxonomy is not None:
            entry["taxonomy"] = rule.taxonomy
        replaced = False
        for i, existing in enumerate(rules_list):
            if isinstance(existing, dict) and existing.get("id") == rule.id:
                rules_list[i] = entry
                replaced = True
                break
        if not replaced:
            rules_list.append(entry)
        index["rules"] = rules_list
        self.save_index(index)

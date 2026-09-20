"""Promotion executor — writes rule YAML, updates index, git commit."""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cauterule.log import get_logger
from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import Provenance, StandingRule
from cauterule.serialization.rule_yaml import dump_rule_to_file
from cauterule.store.git import git_commit
from cauterule.store.index import IndexManager

_log = get_logger(__name__)

_RULE_ID_PREFIX = "R"


@contextmanager
def _promotion_lock(rules_dir: Path) -> Iterator[None]:
    """Serialize rule-ID allocation across processes (#778).

    A cross-process ``flock`` ensures the scan-then-write window cannot be
    interleaved by a concurrent promotion.
    """
    rules_dir.mkdir(parents=True, exist_ok=True)
    lock_path = rules_dir / ".promotion.lock"
    with lock_path.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _reserve_rule_id(rules_dir: Path) -> tuple[str, Path]:
    """Allocate the next ID and atomically reserve its file (#778).

    Must be called while holding :func:`_promotion_lock`; ``O_EXCL`` turns a
    lost race into a loud error instead of a silent overwrite.
    """
    rule_id = _next_rule_id(rules_dir)
    rule_path = rules_dir / f"{rule_id}.yaml"
    try:
        fd = os.open(rule_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise ValueError(f"rule id collision: {rule_id} already exists") from exc
    os.close(fd)
    return rule_id, rule_path


def _normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _find_active_duplicate(rules_dir: Path, candidate: CandidateRule) -> StandingRule | None:
    """Return an existing active rule with the same normalized when/do (#780)."""
    from cauterule.serialization.rule_yaml import load_rules_from_dir

    for rule in load_rules_from_dir(rules_dir, quarantine=False):
        if rule.status != "active":
            continue
        if (
            _normalize(rule.when.trigger) == _normalize(candidate.when.trigger)
            and _normalize(rule.do.directive) == _normalize(candidate.do.directive)
            and _normalize(rule.when.signature or "") == _normalize(candidate.when.signature or "")
        ):
            return rule
    return None


def _next_rule_id(rules_dir: Path) -> str:
    existing: list[int] = []
    if rules_dir.is_dir():
        for p in rules_dir.glob("*.yaml"):
            if p.stem.startswith(f"{_RULE_ID_PREFIX}-"):
                try:
                    existing.append(int(p.stem[len(_RULE_ID_PREFIX) + 1 :]))
                except ValueError:
                    continue
        for p in rules_dir.glob("*.yml"):
            if p.stem.startswith(f"{_RULE_ID_PREFIX}-"):
                try:
                    existing.append(int(p.stem[len(_RULE_ID_PREFIX) + 1 :]))
                except ValueError:
                    continue
    seq = max(existing) + 1 if existing else 1
    return f"{_RULE_ID_PREFIX}-{seq:03d}"


def execute_promotion(
    candidate: CandidateRule,
    config: dict[str, Any],
) -> str:
    """Persist *candidate* as a promoted rule and commit it.

    Config keys (* = required):

    * ``rules_dir`` — directory to write rule YAML files into.
    * ``source_trajectory`` — trajectory identifier for provenance.
    * ``extracted_by`` — extractor identifier for provenance.
    * ``extract_timestamp`` — ISO timestamp for provenance.
    * ``extraction_pass`` — extraction pass number.
    * ``promotion_mode`` — promotion mode label (e.g. ``"auto"``).
    * ``status`` — initial rule status (default ``"active"``).

    Args:
        candidate: The candidate rule to promote.
        config: Configuration dict (see above).

    Returns:
        The promoted rule ID (e.g. ``"R-001"``).

    Raises:
        ValueError: If required config keys are missing.
    """
    rules_dir = Path(str(config.get("rules_dir", "rules")))

    # Taxonomy gate (#586): required field, auto-classified when missing.
    from cauterule.taxonomy import ensure_taxonomy

    taxonomy, _auto = ensure_taxonomy(
        candidate.when.trigger,
        candidate.do.directive,
        config.get("taxonomy"),
    )

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    promoted_at = now

    provenance = Provenance(
        source_trajectory=str(config["source_trajectory"]),
        extracted_by=str(config["extracted_by"]),
        extract_timestamp=str(config["extract_timestamp"]),
        extraction_pass=int(config.get("extraction_pass", 1)),
        promotion_commit=None,
        promotion_mode=str(config.get("promotion_mode", "auto")),
    )

    # Idempotency (#780): never create a second active duplicate of the same
    # rule. Return the existing rule's id instead.
    existing = _find_active_duplicate(rules_dir, candidate)
    if existing is not None:
        _log.info(
            "promotion skipped for %r: duplicate of %s",
            candidate.when.trigger,
            existing.id,
        )
        return existing.id

    # Allocate + reserve the rule file atomically, and only after the required
    # config was validated (a missing key must not leave an empty reservation).
    with _promotion_lock(rules_dir):
        rule_id, rule_path = _reserve_rule_id(rules_dir)

    rule = StandingRule(
        id=rule_id,
        when=candidate.when,
        do=candidate.do,
        confidence=candidate.confidence,
        provenance=provenance,
        status=str(config.get("status", "active")),  # type: ignore[arg-type]
        promoted_at=promoted_at,
        template=candidate.template,
        taxonomy=taxonomy,
    )

    rules_dir.mkdir(parents=True, exist_ok=True)
    dump_rule_to_file(rule, str(rule_path))

    index_mgr = IndexManager(str(rules_dir))
    try:
        index_mgr.add_entry(rule)
    except Exception:
        # Best-effort index sync (code-review): a corrupt index must not
        # abort the promotion after the rule file was already written.
        _log.warning("index sync failed for %s — continuing without index entry", rule_id)

    commit_hash = git_commit(f"promote: {rule_id}", str(rules_dir))
    if commit_hash is None:
        # #505: explicit handling — promotion stands, but provenance records
        # the missing commit instead of silently implying success.
        _log.warning("promotion commit failed for %s — continuing without hash", rule_id)

    final_provenance = Provenance(
        source_trajectory=provenance.source_trajectory,
        extracted_by=provenance.extracted_by,
        extract_timestamp=provenance.extract_timestamp,
        extraction_pass=provenance.extraction_pass,
        promotion_commit=commit_hash,
        promotion_mode=provenance.promotion_mode,
    )
    rule = StandingRule(
        id=rule_id,
        when=candidate.when,
        do=candidate.do,
        confidence=candidate.confidence,
        provenance=final_provenance,
        status=rule.status,
        promoted_at=rule.promoted_at,
        template=candidate.template,
        taxonomy=taxonomy,
    )
    dump_rule_to_file(rule, str(rule_path))

    if commit_hash:
        git_commit(f"promote: {rule_id} (update hash)", str(rules_dir))

    # Promotion webhook (#585): best-effort — delivery failure must never
    # block or roll back a completed promotion.
    try:
        from cauterule.integrations.webhook import notify_promotion

        notify_promotion(
            {
                "id": rule_id,
                "title": candidate.do.directive[:120],
                "trigger": candidate.when.trigger,
                "promoted_at": promoted_at,
                "promoted_by": provenance.promotion_mode,
            },
            store_dir=str(rules_dir),
        )
    except Exception:
        _log.warning("promotion webhook failed for %s — continuing", rule_id)

    # OTEL rule.promote span (#588): best-effort, never blocks promotion.
    try:
        from cauterule.integrations.otel import OtelExporter

        OtelExporter().emit_rule_promote(rule_id, justification=provenance.promotion_mode or "")
    except Exception:
        _log.warning("OTel promote emit failed for %s — continuing", rule_id)

    return rule_id

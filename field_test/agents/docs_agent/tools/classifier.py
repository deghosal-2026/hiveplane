from __future__ import annotations
import logging
from typing import Any

from state import ConventionalCommit, ClassifiedCommit, CommitType

logger = logging.getLogger(__name__)

TRIVIAL_TYPES = {CommitType.chore, CommitType.ci, CommitType.test}

MERGE_PREFIXES = ("merge pull request", "merge branch", "merge remote-tracking branch")


def _is_merge_commit(commit: ConventionalCommit) -> bool:
    desc = (commit.get("description") or "").lower()
    return any(desc.startswith(p) for p in MERGE_PREFIXES)


def rules_based_pass(commit: ConventionalCommit) -> ClassifiedCommit | None:
    if _is_merge_commit(commit):
        return ClassifiedCommit(
            commit=commit,
            is_notable=False,
            confidence=1.0,
            rationale="Merge commit — excluded from release notes.",
        )
    if commit["is_breaking"]:
        return ClassifiedCommit(
            commit=commit,
            is_notable=True,
            confidence=1.0,
            rationale="Breaking change — always notable.",
        )
    t = commit["type"]
    if t in TRIVIAL_TYPES:
        return ClassifiedCommit(
            commit=commit,
            is_notable=False,
            confidence=0.9,
            rationale=f"Type '{t.value}' is typically trivial maintenance work.",
        )
    if t == CommitType.feat:
        return ClassifiedCommit(
            commit=commit,
            is_notable=True,
            confidence=0.8,
            rationale="Feature commit — likely user-facing change.",
        )
    return None


def classify_commits(
    commits: list[ConventionalCommit],
    llm_client: Any,
) -> list[ClassifiedCommit]:
    results: list[ClassifiedCommit] = []
    ambiguous: list[ConventionalCommit] = []

    for c in commits:
        result = rules_based_pass(c)
        if result is not None:
            results.append(result)
        else:
            ambiguous.append(c)

    if not ambiguous:
        return results

    CHUNK_SIZE = 20
    total_chunks = (len(ambiguous) + CHUNK_SIZE - 1) // CHUNK_SIZE
    import sys
    for chunk_idx, chunk_start in enumerate(range(0, len(ambiguous), CHUNK_SIZE)):
        chunk = ambiguous[chunk_start:chunk_start + CHUNK_SIZE]
        print(f"  Classifying chunk {chunk_idx+1}/{total_chunks} ({len(chunk)} commits)...", file=sys.stderr, flush=True)
        try:
            results.extend(_classify_chunk(chunk, llm_client))
        except Exception as e:
            logger.warning("LLM classification failed for chunk %d: %s. Defaulting to notable.", chunk_start // CHUNK_SIZE, e)
            for c in chunk:
                results.append(ClassifiedCommit(
                    commit=c, is_notable=True, confidence=0.5, rationale="LLM unavailable — defaulted to notable"
                ))

    return results


def _classify_chunk(chunk: list[ConventionalCommit], llm_client: Any) -> list[ClassifiedCommit]:
    batch_prompt = _build_classify_prompt(chunk)
    response = llm_client.complete_structured(
        system_prompt=(
            "You are a commit classifier. Determine if each commit is 'notable' "
            "(user-facing change worth mentioning in release notes) or 'trivial' "
            "(internal refactoring, minor fixes, automation). "
            "Return a JSON object with commit index as key and value as "
            '{"is_notable": bool, "confidence": float, "rationale": str}.'
        ),
        user_prompt=batch_prompt,
        max_tokens=4096,
    )
    chunk_results: list[ClassifiedCommit] = []
    for i, c in enumerate(chunk):
        entry = response.get(str(i), response.get(i, {}))
        if isinstance(entry, dict):
            chunk_results.append(ClassifiedCommit(
                commit=c,
                is_notable=bool(entry.get("is_notable", True)),
                confidence=float(entry.get("confidence", 0.5)),
                rationale=str(entry.get("rationale", "")),
            ))
        else:
            chunk_results.append(ClassifiedCommit(
                commit=c, is_notable=True, confidence=0.5, rationale="LLM response parse failure"
            ))
    return chunk_results


def _build_classify_prompt(commits: list[ConventionalCommit]) -> str:
    lines: list[str] = []
    for i, c in enumerate(commits):
        lines.append(f"[{i}] type={c['type'].value} scope={c['scope']} desc={c['description']}")
    return "\n".join(lines)

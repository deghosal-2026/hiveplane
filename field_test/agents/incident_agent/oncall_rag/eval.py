from __future__ import annotations

import json
from pathlib import Path

from oncall_rag.config import Config
from oncall_rag.retriever import retrieve


def _parse_lines(spec: str) -> tuple[int, int]:
    spec = spec.strip()
    if "-" in spec:
        start, end = spec.split("-", 1)
        return int(start), int(end)
    val = int(spec)
    return val, val


def _is_match(chunk_source: str, chunk_start: int, chunk_end: int, q: dict) -> bool:
    if chunk_source != q.get("source_file", ""):
        return False
    source_lines = q.get("source_lines")
    if not source_lines:
        return True
    eval_start, eval_end = _parse_lines(str(source_lines))
    return not (chunk_end < eval_start or chunk_start > eval_end)


def run_eval(config: Config, eval_path: str = "data/eval.json") -> dict:
    path = Path(eval_path)
    if not path.exists():
        return {"mrr": 0.0, "recall_at_5": 0.0, "error": f"Eval file not found: {eval_path}"}

    data = json.loads(path.read_text())
    questions = data.get("questions", [])

    if not questions:
        return {"mrr": 0.0, "recall_at_5": 0.0, "error": "No questions in eval set"}

    reciprocal_ranks = []
    recall_at_5 = 0

    for q in questions:
        chunks = retrieve(q["question"], config)
        rank = 0
        found = False
        for i, c in enumerate(chunks):
            if _is_match(c.source_file, c.start_line, c.end_line, q):
                rank = i + 1
                found = True
                break
        if found:
            reciprocal_ranks.append(1.0 / rank)
            if rank <= 5:
                recall_at_5 += 1
        else:
            reciprocal_ranks.append(0.0)

    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0
    recall = recall_at_5 / len(questions) if questions else 0.0

    return {"mrr": round(mrr, 4), "recall_at_5": round(recall, 4)}

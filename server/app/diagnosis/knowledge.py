"""可审计的静态系统知识检索；知识不能替代运行时证据。"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict[str, Any]]:
    path = KNOWLEDGE_ROOT / "catalog.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {item["knowledge_id"]: item for item in raw}


def retrieve_knowledge(
    query: str,
    findings: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    catalog = load_catalog()
    explicit_ids = {
        knowledge_id
        for finding in findings
        for knowledge_id in finding.get("knowledge_ids", [])
    }
    terms = _terms(" ".join([
        query,
        *[str(item.get("finding_type", "")) for item in findings],
        *[str(item.get("summary", "")) for item in findings],
    ]))
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for knowledge_id, item in catalog.items():
        haystack = _terms(" ".join([
            item.get("title", ""),
            item.get("summary", ""),
            " ".join(item.get("keywords", [])),
        ]))
        score = len(terms & haystack)
        if knowledge_id in explicit_ids:
            score += 100
        if score > 0 and (knowledge_id in explicit_ids or score >= 2):
            ranked.append((score, knowledge_id, item))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [
        {
            "knowledge_id": knowledge_id,
            "title": item["title"],
            "summary": item["summary"],
            "applies_to": item.get("applies_to", []),
            "required_evidence": item.get("required_evidence", []),
            "caveats": item.get("caveats", []),
            "document": item["document"],
        }
        for _, knowledge_id, item in ranked[:limit]
    ]


def knowledge_ids() -> set[str]:
    return set(load_catalog())


def _terms(value: str) -> set[str]:
    lowered = value.lower()
    ascii_terms = set(re.findall(r"[a-z0-9_.-]{2,}", lowered))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    chinese_terms: set[str] = set(chinese_runs)
    for run in chinese_runs:
        chinese_terms.update(run[index:index + 2] for index in range(0, max(1, len(run) - 1)))
        chinese_terms.update(char for char in run)
    return ascii_terms | chinese_terms

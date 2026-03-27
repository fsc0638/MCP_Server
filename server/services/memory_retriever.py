"""Phase 2-C: Deterministic memory retrieval for prompt injection.

Goal:
- Given a user query, retrieve the most relevant memory snippets from:
  - memory/memory_store.json (long_term behavior rules)
  - memory/learning_snapshot.json (optional)

No external APIs.

This is a simple keyword scoring retriever (v1).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Tuple


def _tokenize(s: str) -> List[str]:
    s = (s or "").lower()
    # keep CJK chars as tokens too
    parts = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", s)
    return [p for p in parts if p and p.strip()]


@dataclass
class MemoryRetriever:
    project_root: Path

    def _load_json(self, rel: str) -> Dict[str, Any]:
        p = self.project_root / rel
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def retrieve(self, query: str, max_items: int = 8) -> List[Dict[str, Any]]:
        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return []

        store = self._load_json("memory/memory_store.json")
        br = (((store.get("long_term") or {}).get("behavior_rules")) or {})

        candidates: List[Tuple[int, Dict[str, Any]]] = []

        def add_items(items: list, kind: str):
            for it in items or []:
                txt = (it.get("text") if isinstance(it, dict) else str(it))
                tokens = set(_tokenize(txt))
                score = len(q_tokens & tokens)
                if score <= 0:
                    continue
                candidates.append((score, {"kind": kind, "text": txt}))

        add_items(br.get("style"), "style")
        add_items(br.get("taboos"), "taboos")
        add_items(br.get("group_rules"), "group_rules")

        candidates.sort(key=lambda x: x[0], reverse=True)
        out = [c[1] for c in candidates[:max_items]]
        return out


def render_memory_injection(items: List[Dict[str, Any]], max_chars: int = 800, exclude_texts: List[str] | None = None) -> str:
    if not items:
        return ""
    exclude = set((t or "").strip().lower() for t in (exclude_texts or []) if (t or "").strip())

    lines = ["\n【相關記憶（自動檢索）】\n"]
    for it in items:
        t = (it.get('text') or '').strip()
        if t.lower() in exclude:
            continue
        lines.append(f"- ({it.get('kind')}) {t}\n")
    text = "".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n(…已截斷)\n"
    return text

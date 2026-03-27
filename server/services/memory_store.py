"""Phase 2-B: Structured memory store (short-term + long-term).

Goals:
- Deterministic, testable memory persistence.
- Separate stable long-term rules from volatile short-term observations.
- No external API required.

Files:
- memory/memory_store.json

Data model (v1):
{
  "version": 1,
  "updated_at": "...",
  "long_term": {
     "behavior_rules": {"style": [...], "taboos": [...], "group_rules": [...]},
     "notes": [...]
  },
  "short_term": {
     "recent_ticks": [...],
     "recent_notes": [...]
  }
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class MemoryStore:
    project_root: Path

    @property
    def store_path(self) -> Path:
        return self.project_root / "memory" / "memory_store.json"

    def load(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return {
                "version": 1,
                "updated_at": None,
                "long_term": {"behavior_rules": {"style": [], "taboos": [], "group_rules": []}, "notes": []},
                "short_term": {"recent_ticks": [], "recent_notes": []},
            }
        return json.loads(self.store_path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        data["version"] = 1
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.store_path)

    def upsert_long_term_behavior_rules(self, behavior_rules: Dict[str, Any], limit_each: int = 60) -> Dict[str, Any]:
        data = self.load()
        lt = data.setdefault("long_term", {})
        br = lt.setdefault("behavior_rules", {"style": [], "taboos": [], "group_rules": []})

        def _merge(existing: List[dict], incoming: List[dict]) -> List[dict]:
            out: List[dict] = []
            seen = set()
            for it in (incoming or []) + (existing or []):
                txt = (it.get("text") if isinstance(it, dict) else str(it)).strip()
                if not txt:
                    continue
                key = txt.lower()
                if key in seen:
                    continue
                seen.add(key)
                if isinstance(it, dict):
                    out.append(it)
                else:
                    out.append({"text": txt})
                if len(out) >= limit_each:
                    break
            return out

        br["style"] = _merge(br.get("style", []), behavior_rules.get("style", []))
        br["taboos"] = _merge(br.get("taboos", []), behavior_rules.get("taboos", []))
        br["group_rules"] = _merge(br.get("group_rules", []), behavior_rules.get("group_rules", []))

        self.save(data)
        return data

    def append_short_term_tick(self, tick_summary: Dict[str, Any], limit: int = 50) -> Dict[str, Any]:
        data = self.load()
        st = data.setdefault("short_term", {})
        arr = st.setdefault("recent_ticks", [])
        arr.append(tick_summary)
        st["recent_ticks"] = arr[-limit:]
        self.save(data)
        return data

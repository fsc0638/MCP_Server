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
                "change_log": [],
            }
        return json.loads(self.store_path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        data["version"] = 1
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.store_path)

    def _append_change(self, data: Dict[str, Any], event: Dict[str, Any], limit: int = 1000) -> None:
        log = data.setdefault("change_log", [])
        log.append(event)
        data["change_log"] = log[-limit:]

    def upsert_long_term_behavior_rules(self, behavior_rules: Dict[str, Any], limit_each: int = 60, history_limit: int = 1000) -> Dict[str, Any]:
        data = self.load()
        lt = data.setdefault("long_term", {})
        br = lt.setdefault("behavior_rules", {"style": [], "taboos": [], "group_rules": []})

        def _merge(kind: str, existing: List[dict], incoming: List[dict]) -> List[dict]:
            # Build index for existing
            idx = {}
            for it in existing or []:
                if not isinstance(it, dict):
                    it = {"text": str(it)}
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                idx[txt.lower()] = it

            # Apply incoming with overwrite + change log
            for it in incoming or []:
                if not isinstance(it, dict):
                    it = {"text": str(it)}
                txt = (it.get("text") or "").strip()
                if not txt:
                    continue
                key = txt.lower()
                prev = idx.get(key)
                if prev is None:
                    idx[key] = it
                    self._append_change(
                        data,
                        {
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "type": "add",
                            "kind": kind,
                            "text": txt,
                            "new": it,
                        },
                        limit=history_limit,
                    )
                else:
                    # overwrite if different metadata
                    if prev != it:
                        idx[key] = it
                        self._append_change(
                            data,
                            {
                                "ts": datetime.now().isoformat(timespec="seconds"),
                                "type": "overwrite",
                                "kind": kind,
                                "text": txt,
                                "old": prev,
                                "new": it,
                            },
                            limit=history_limit,
                        )

            # Return deterministic order: incoming first, then the rest; cap to limit_each
            out: List[dict] = []
            seen = set()
            for it in (incoming or []) + list(idx.values()):
                txt = (it.get("text") if isinstance(it, dict) else str(it)).strip()
                if not txt:
                    continue
                key = txt.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(it if isinstance(it, dict) else {"text": txt})
                if len(out) >= limit_each:
                    break
            return out

        br["style"] = _merge("style", br.get("style", []), behavior_rules.get("style", []))
        br["taboos"] = _merge("taboos", br.get("taboos", []), behavior_rules.get("taboos", []))
        br["group_rules"] = _merge("group_rules", br.get("group_rules", []), behavior_rules.get("group_rules", []))

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

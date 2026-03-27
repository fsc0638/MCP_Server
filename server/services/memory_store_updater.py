"""Phase 2-B: Update memory_store.json from existing artifacts.

Sources:
- memory/behavior_rules.json (authoritative for long_term behavior rules)
- memory/continuous_learner_state.json (tick summary -> short_term)

This is designed to run in the scheduled learner tick.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from server.services.memory_store import MemoryStore


def update_memory_store(project_root: str | Path) -> Dict[str, Any]:
    root = Path(project_root)
    ms = MemoryStore(root)

    # Long-term behavior rules
    br_path = root / "memory" / "behavior_rules.json"
    if br_path.exists():
        br = json.loads(br_path.read_text(encoding="utf-8"))
        # disable_missing=True so removed rules stop being injected
        ms.upsert_long_term_behavior_rules(br, disable_missing=True, history_limit=1000)

    # Short-term tick summary
    st_path = root / "memory" / "continuous_learner_state.json"
    if st_path.exists():
        st = json.loads(st_path.read_text(encoding="utf-8"))
        tick = {
            "last_run_at": st.get("last_run_at"),
            "last_recent_count": st.get("last_recent_count"),
            "last_file_count": st.get("last_file_count"),
            "last_mode": st.get("last_mode"),
        }
        ms.append_short_term_tick(tick, limit=50, max_age_days=7)

    return ms.load()

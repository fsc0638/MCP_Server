"""Phase 3.2: Prompt meta logger (jsonl) for debugging.

When PROMPT_DEBUG=1, append prompt_meta to:
- workspace/sessions/{session_id}_prompt_meta.jsonl

This helps post-mortem analysis without spamming logs.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def append_prompt_meta(project_root: str | Path, session_id: str, meta: Dict[str, Any]) -> None:
    root = Path(project_root)
    sessions_dir = root / "workspace" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    p = sessions_dir / f"{session_id}_prompt_meta.jsonl"
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "meta": meta,
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

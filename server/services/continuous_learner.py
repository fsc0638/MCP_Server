"""Phase 3: Continuous learner (scheduled).

Goal:
- Run a lightweight, periodic learning loop every N minutes.
- Must be safe to run without external API dependencies by default.
- When an LLM callable is available, it can be used to extract structured
  learnings (future Phase 2 memory integration).

Design principles:
- Idempotent per tick (track last_seen pointers + content hashes).
- Never crash the whole app; log and return.
- Default behavior should be no-op if prerequisites are missing.

Phase 3 learning scope (user-defined):
1) workspace/sessions (conversation history + msg_cache)
2) workspace/profiles (scheduled profile updates)
3) Agent_workspace/line_uploads (uploaded files)

Extraction policy:
- Text and office docs are extracted (PDF/DOCX/XLSX/CSV + plain text).
- Images are skipped.
"""

from __future__ import annotations

import json
import logging
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, List, Tuple

from server.services.file_extractor import extract_file_content

logger = logging.getLogger("MCP_Server.ContinuousLearner")

LLMCallable = Callable[[str, Dict[str, Any]], Dict[str, Any]]


@dataclass
class ContinuousLearnerConfig:
    interval_minutes: int = 10
    enabled: bool = True


class ContinuousLearner:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.state_path = self.project_root / "memory" / "continuous_learner_state.json"
        self.buffer_path = self.project_root / "memory" / "continuous_learning_buffer.jsonl"
        self.memory_md_path = self.project_root / "memory" / "MEMORY.md"

    def _utcnow_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def load_state(self) -> dict:
        try:
            if self.state_path.exists():
                return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[ContinuousLearner] Failed to load state: {e}")
        return {}

    def save_state(self, state: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception as e:
            logger.error(f"[ContinuousLearner] Failed to save state: {e}")

    def _append_jsonl(self, record: dict) -> None:
        try:
            self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.buffer_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"[ContinuousLearner] Failed to append buffer: {e}")

    def _append_memory_md(self, lines: List[str]) -> None:
        try:
            self.memory_md_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.memory_md_path.exists():
                self.memory_md_path.write_text(
                    "# MCP Server — Session Memory\n\n---\n\n",
                    encoding="utf-8",
                )
            with open(self.memory_md_path, "a", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            logger.error(f"[ContinuousLearner] Failed to append MEMORY.md: {e}")

    def _sha1_text(self, s: str) -> str:
        return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

    # ── Source 1: workspace/sessions ─────────────────────────────────────────

    def _load_recent_messages(self, since_ts: int) -> List[dict]:
        out: List[dict] = []
        sessions_dir = self.project_root / "workspace" / "sessions"
        if not sessions_dir.exists():
            return out

        for p in sessions_dir.glob("*.json"):
            if p.name.endswith("_meta.json") or p.name.endswith("_msg_cache.json"):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    continue
                for m in data:
                    if not isinstance(m, dict):
                        continue
                    ts = int(m.get("created_at") or 0)
                    if ts >= since_ts and m.get("role") in ("user", "assistant"):
                        out.append(
                            {
                                "session_id": p.stem,
                                "role": m.get("role"),
                                "content": m.get("content", ""),
                                "created_at": ts,
                            }
                        )
            except Exception:
                continue

        out.sort(key=lambda x: (x.get("created_at", 0), x.get("session_id", "")))
        return out

    # ── Source 2/3: file scanning ────────────────────────────────────────────

    def _is_image(self, path: Path) -> bool:
        return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tiff"}

    def _scan_files(self, root: Path, since_mtime: float, recursive: bool = True) -> List[Path]:
        if not root.exists():
            return []
        it = root.rglob("*") if recursive else root.glob("*")
        out: List[Path] = []
        for p in it:
            try:
                if not p.is_file():
                    continue
                if self._is_image(p):
                    continue
                mt = p.stat().st_mtime
                if mt >= since_mtime:
                    out.append(p)
            except Exception:
                continue
        out.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0)
        return out

    def _extract_file_record(self, path: Path) -> Tuple[dict, Optional[str]]:
        text, err = extract_file_content(str(path))
        preview = text[:4000]
        rec = {
            "type": "file",
            "path": str(path.relative_to(self.project_root)) if str(path).startswith(str(self.project_root)) else str(path),
            "mtime": path.stat().st_mtime,
            "bytes": path.stat().st_size,
            "text_sha1": self._sha1_text(text) if text else None,
            "preview": preview,
        }
        return rec, err

    def tick(self, llm_callable: Optional[LLMCallable] = None) -> None:
        """One learning tick.

        Step 1: sessions messages (already implemented)
        Step 2: profiles + line_uploads (file extraction; images skipped)

        Output:
        - memory/continuous_learning_buffer.jsonl (raw)
        - memory/MEMORY.md (brief human summary)
        """
        state = self.load_state()
        now_iso = self._utcnow_iso()
        now_ts = int(datetime.now(timezone.utc).timestamp())

        # ---- Step 1: sessions ----
        last_ts = int(state.get("last_seen_ts") or 0)
        if last_ts <= 0:
            last_ts = now_ts - 600
        recent_msgs = self._load_recent_messages(last_ts)

        # ---- Step 2: files (profiles + uploads + msg_cache) ----
        last_mtime = float(state.get("last_seen_mtime") or 0.0)
        if last_mtime <= 0:
            last_mtime = float(datetime.now(timezone.utc).timestamp() - 600)

        profiles_root = self.project_root / "workspace" / "profiles"
        uploads_root = self.project_root / "Agent_workspace" / "line_uploads"
        sessions_root = self.project_root / "workspace" / "sessions"

        changed_files: List[Path] = []
        changed_files += self._scan_files(profiles_root, last_mtime, recursive=True)
        changed_files += self._scan_files(uploads_root, last_mtime, recursive=True)
        # include msg_cache JSONs under sessions
        for p in self._scan_files(sessions_root, last_mtime, recursive=False):
            if p.name.endswith("_msg_cache.json"):
                changed_files.append(p)

        # Dedup by absolute path
        uniq = {}
        for p in changed_files:
            uniq[str(p.resolve())] = p
        changed_files = list(uniq.values())
        changed_files.sort(key=lambda x: x.stat().st_mtime)

        # ---- Update state ----
        state["last_run_at"] = now_iso
        state.setdefault("runs", 0)
        state["runs"] += 1
        state["last_mode"] = "with_llm" if llm_callable else "no_llm"
        state["last_seen_ts"] = now_ts
        state["last_recent_count"] = len(recent_msgs)
        state["last_seen_mtime"] = max([last_mtime] + [p.stat().st_mtime for p in changed_files]) if changed_files else last_mtime
        state["last_file_count"] = len(changed_files)

        # ---- Persist buffer ----
        self._append_jsonl(
            {
                "type": "continuous_learning_tick",
                "at": now_iso,
                "since_ts": last_ts,
                "since_mtime": last_mtime,
                "message_count": len(recent_msgs),
                "file_count": len(changed_files),
            }
        )

        for m in recent_msgs:
            self._append_jsonl({"type": "message", **m})

        file_errors = 0
        for p in changed_files:
            rec, err = self._extract_file_record(p)
            if err:
                rec["error"] = err
                file_errors += 1
            self._append_jsonl(rec)

        # ---- MEMORY.md brief summary ----
        sample_user = next((x for x in reversed(recent_msgs) if x["role"] == "user" and x.get("content")), None)
        sample_text = (sample_user.get("content", "")[:80] + "…") if sample_user else "(no user sample)"
        md_lines = [
            f"\n## Continuous Learner Tick — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"- since_ts: {last_ts}\n",
            f"- messages: {len(recent_msgs)}\n",
            f"- files_changed: {len(changed_files)} (errors: {file_errors})\n",
            f"- sample_user: {sample_text}\n",
        ]
        if changed_files:
            md_lines.append("- changed_files (top 5):\n")
            for p in changed_files[:5]:
                rel = str(p.relative_to(self.project_root)) if str(p).startswith(str(self.project_root)) else str(p)
                md_lines.append(f"  - {rel}\n")
        md_lines.append("---\n")
        self._append_memory_md(md_lines)

        self.save_state(state)
        logger.info(
            f"[ContinuousLearner] Tick complete runs={state['runs']} msgs={len(recent_msgs)} files={len(changed_files)} mode={state['last_mode']}"
        )

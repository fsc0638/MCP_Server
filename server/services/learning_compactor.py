"""Phase 3: Learning compactor.

Takes raw JSONL events produced by ContinuousLearner and produces a compact,
structured snapshot for downstream use.

Outputs:
- memory/learning_snapshot.json (structured)
- memory/learning_snapshot.md (human-readable)

Design:
- Deterministic, no external API required.
- Dedup by (kind,key) with policy-based conflict resolution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Iterable

from server.services.learning_policy import LearningPolicy

logger = logging.getLogger("MCP_Server.LearningCompactor")


@dataclass
class LearningCompactor:
    project_root: Path
    policy: LearningPolicy = LearningPolicy()

    @property
    def buffer_path(self) -> Path:
        return self.project_root / "memory" / "continuous_learning_buffer.jsonl"

    @property
    def snapshot_path(self) -> Path:
        return self.project_root / "memory" / "learning_snapshot.json"

    @property
    def snapshot_md_path(self) -> Path:
        return self.project_root / "memory" / "learning_snapshot.md"

    def _iter_jsonl(self) -> Iterable[Dict[str, Any]]:
        if not self.buffer_path.exists():
            return []
        with open(self.buffer_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue

    def build_snapshot(self, limit_messages: int = 200, limit_files: int = 200) -> Dict[str, Any]:
        """Build a compact snapshot from the raw buffer."""
        latest: Dict[str, Dict[str, Any]] = {}
        messages = []
        files = []

        for rec in self._iter_jsonl():
            rtype = rec.get("type")
            if rtype == "message":
                # sessions source
                m = {
                    "source": "sessions",
                    "session_id": rec.get("session_id"),
                    "role": rec.get("role"),
                    "content": rec.get("content"),
                    "created_at": rec.get("created_at"),
                }
                messages.append(m)
            elif rtype == "file":
                path = rec.get("path") or ""
                source = "uploads" if path.startswith("Agent_workspace/line_uploads/") else "profiles"
                fitem = {
                    "source": source,
                    "path": path,
                    "mtime": rec.get("mtime"),
                    "bytes": rec.get("bytes"),
                    "text_sha1": rec.get("text_sha1"),
                    "preview": rec.get("preview"),
                    "error": rec.get("error"),
                }
                files.append(fitem)

                # Dedup key: file path
                key = f"file:{path}"
                if key not in latest:
                    latest[key] = fitem
                else:
                    latest[key] = self.policy.choose(latest[key], fitem)

        # Trim
        messages = messages[-limit_messages:]
        files = files[-limit_files:]

        snap = {
            "policy": {
                "priority": "profiles > sessions > uploads",
                "weights": {
                    "profiles": self.policy.w_profiles,
                    "sessions": self.policy.w_sessions,
                    "uploads": self.policy.w_uploads,
                },
            },
            "counts": {
                "messages": len(messages),
                "files": len(files),
                "dedup_keys": len(latest),
            },
            "messages": messages,
            "files": files,
            "latest": latest,
        }
        return snap

    def write_snapshot(self) -> Dict[str, Any]:
        snap = self.build_snapshot()
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

        # Human readable
        lines = []
        lines.append("# Learning Snapshot\n\n")
        lines.append("## Policy\n")
        lines.append(f"- priority: {snap['policy']['priority']}\n")
        lines.append(f"- weights: {snap['policy']['weights']}\n\n")
        lines.append("## Counts\n")
        lines.append(f"- messages: {snap['counts']['messages']}\n")
        lines.append(f"- files: {snap['counts']['files']}\n")
        lines.append(f"- dedup_keys: {snap['counts']['dedup_keys']}\n\n")
        lines.append("## Recent files (top 10)\n")
        for f in snap["files"][-10:]:
            lines.append(f"- [{f['source']}] {f['path']}\n")
        self.snapshot_md_path.write_text("".join(lines), encoding="utf-8")

        logger.info("[LearningCompactor] Snapshot written")
        return snap

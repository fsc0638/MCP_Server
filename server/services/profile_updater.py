"""
Profile Updater Service — Phase B + C
Manages User/Group Profile deep reasoning, signal collection, and scheduled updates.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MCP_Server.ProfileUpdater")

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_PROFILES_PER_RUN = 20
SIGNALS_RETENTION_DAYS = 7
MAX_MESSAGES_PER_UPDATE = 60
MIN_MESSAGES_PER_UPDATE = 30

# ── Deep Reasoning Prompt (Final Version — v2 + supplements) ─────────────────

_PROFILE_PROMPT_TEMPLATE = """\
你是一個頂級的使用者行為分析專家。
你的任務是根據「現有 Profile」與「最近對話紀錄」進行深度推理並迭代更新 Profile。

【核心原則】
1. 零幻覺：絕不捏造資訊。資訊不足者必須標記「待觀察」。
2. 證據導向：任何推斷必須有對話原句作為支撐。
3. 演化視角：重點捕捉習慣的「變化」與「糾正」過程。
4. 精煉輸出：每區塊最多 8 條，優先高信心度 + 高行為修正價值條目。

【分析維度】
1. 身份與職責（從任務與語境推斷，非單純自述）
2. 語言偏好與溝通風格（正式/非正式、指令型/探討型、詳細/簡潔）
3. 常用任務類型與頻率（提煉高頻場景）
4. 修正信號（否定的產出、要求重做或微調的具體指令）
5. 隱性偏好（透過反覆行為模式展露，未明說）
6. 狀態變更（與舊 Profile 比對，改變或推翻了什麼）
{group_supplement}

【工作流與輸出格式】
### 階段一：深度分析草稿（<thinking> 標籤包覆）
<thinking>
- 證據提取：（每維度最多 3 句代表性原句）
- 衝突檢測：（新對話與舊 Profile 的一致或矛盾之處）
- 綜合推論：（對應 6 個維度進行推演）
</thinking>

### 階段二：更新版 Profile（Markdown）
#### [身份與職責]
#### [偏好與風格]
#### [常用任務]
#### [注意事項與禁忌]

---
對話類型：{chat_type}
現有 Profile：
{existing_profile}

最近對話（{message_count} 則，自 {since_date} 起）：
{recent_messages}
"""

_GROUP_SUPPLEMENT = """\
7. 群組成員辨識（依語氣/用詞區分不同成員，無法確定者標記「成員未知」）
   → 群組 Profile 記錄整體特徵；顯著個別成員可在 [成員觀察] 獨立記錄
"""


class ProfileUpdater:
    """Manages profile creation, updates, and signal collection."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.profiles_dir = self.project_root / "workspace" / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.project_root / "workspace" / "sessions"

    # ─── Profile File Paths ───────────────────────────────────────────────────

    def _profile_path(self, session_id: str) -> Path:
        return self.profiles_dir / f"{session_id}.profile.md"

    def _signals_path(self, session_id: str) -> Path:
        return self.profiles_dir / f"{session_id}_signals.jsonl"

    def _meta_path(self, session_id: str) -> Path:
        return self.profiles_dir / f"{session_id}_profile_meta.json"

    # ─── Profile Read/Write ───────────────────────────────────────────────────

    def get_profile(self, session_id: str) -> str:
        """Load existing profile content, or return empty string."""
        path = self._profile_path(session_id)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                pass
        return ""

    def save_profile(self, session_id: str, content: str):
        """Save updated profile content and update metadata."""
        path = self._profile_path(session_id)
        path.write_text(content, encoding="utf-8")
        # Update meta
        meta = self._load_meta(session_id)
        meta["last_updated"] = datetime.now().isoformat()
        meta["version"] = meta.get("version", 0) + 1
        self._save_meta(session_id, meta)
        logger.info(f"Profile updated: {session_id} (v{meta['version']})")

    def _load_meta(self, session_id: str) -> dict:
        path = self._meta_path(session_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"version": 0}

    def _save_meta(self, session_id: str, meta: dict):
        path = self._meta_path(session_id)
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # ─── Signal Collection (Level 1: Preference Learning) ─────────────────────

    def append_signal(self, session_id: str, signal_data: dict):
        """Append a preference signal to the signals log."""
        signal_data["ts"] = datetime.now().isoformat()
        path = self._signals_path(session_id)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal_data, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to append signal for {session_id}: {e}")

    def load_signals(self, session_id: str) -> list:
        """Load all signals within retention period."""
        path = self._signals_path(session_id)
        if not path.exists():
            return []
        cutoff = (datetime.now() - timedelta(days=SIGNALS_RETENTION_DAYS)).isoformat()
        signals = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                        if s.get("ts", "") >= cutoff:
                            signals.append(s)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return signals

    def cleanup_expired_signals(self, session_id: str):
        """Remove signals older than retention period."""
        signals = self.load_signals(session_id)
        path = self._signals_path(session_id)
        try:
            with open(path, "w", encoding="utf-8") as f:
                for s in signals:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ─── Correction Signal Detection ──────────────────────────────────────────

    @staticmethod
    def classify_text_signal(user_text: str) -> Optional[dict]:
        """
        Detect correction/preference signals from user text.
        Returns signal dict or None if no signal detected.
        """
        text = user_text.strip()
        if not text or len(text) > 500:
            return None

        # Strong negative signals
        neg_patterns = [
            "不對", "不是這樣", "錯了", "重來", "重新", "你理解錯",
            "這不是我要的", "不要這樣", "格式不對", "別用這", "不需要",
            "太長了", "太短了", "不要廢話", "去掉", "拿掉",
        ]
        for p in neg_patterns:
            if p in text:
                return {
                    "type": "correction",
                    "signal": "negative",
                    "text": text[:200],
                }

        # Strong positive signals
        pos_patterns = [
            "對", "就是這樣", "完美", "很好", "太棒了", "正確",
            "沒問題", "很讚", "讚",
        ]
        # Positive signals need to be more careful — only trigger for short confirmations
        if len(text) < 30:
            for p in pos_patterns:
                if text.startswith(p) or text == p:
                    return {
                        "type": "correction",
                        "signal": "positive",
                        "text": text[:200],
                    }

        return None

    @staticmethod
    def classify_sticker_signal(
        sticker_keywords: list,
        sticker_text: str,
        vision_description: str = ""
    ) -> Optional[dict]:
        """
        Classify sticker as preference signal.
        Returns signal dict or None.
        """
        signal = {
            "type": "sticker",
            "has_text": bool(sticker_text),
        }

        # If sticker has text, use text for classification
        if sticker_text:
            signal["text"] = sticker_text
            text_lower = sticker_text.lower()
            # Positive text signals
            if any(w in text_lower for w in ["讚", "ok", "好的", "了解", "謝謝", "加油", "辛苦", "nice", "good", "perfect"]):
                signal["signal"] = "positive"
                return signal
            # Negative text signals
            if any(w in text_lower for w in ["不", "no", "嗯？", "咦", "什麼", "蛤"]):
                signal["signal"] = "negative"
                return signal
            # Unclear text — still record as neutral
            signal["signal"] = "neutral"
            return signal

        # No text — use keywords and vision description
        if sticker_keywords:
            signal["keywords"] = sticker_keywords
            kw_str = " ".join(sticker_keywords).lower()
            # Positive keyword patterns
            if any(w in kw_str for w in ["happy", "love", "good", "thumb", "smile", "laugh", "cheer", "great", "joy"]):
                signal["signal"] = "positive"
                return signal
            # Negative keyword patterns
            if any(w in kw_str for w in ["angry", "sad", "cry", "frown", "no", "bad", "shock", "disappointed"]):
                signal["signal"] = "negative"
                return signal
            # Confused patterns
            if any(w in kw_str for w in ["confused", "question", "think", "wonder", "hmm", "doubt"]):
                signal["signal"] = "confused"
                return signal

        # Vision description fallback
        if vision_description:
            signal["vision_desc"] = vision_description[:200]
            desc = vision_description.lower()
            if any(w in desc for w in ["thumbs up", "比讚", "開心", "笑", "讚許", "點頭"]):
                signal["signal"] = "positive"
                return signal
            if any(w in desc for w in ["皺眉", "搖頭", "生氣", "哭", "憤怒", "不滿"]):
                signal["signal"] = "negative"
                return signal
            if any(w in desc for w in ["疑惑", "問號", "歪頭", "思考", "困惑"]):
                signal["signal"] = "confused"
                return signal

        return None

    # ─── Scheduled Profile Update (Phase B2) ──────────────────────────────────

    def run_scheduled_update(self, llm_callable=None):
        """
        Scheduled job: update profiles for all active sessions.
        Called by APScheduler at 09:00 / 12:00 / 17:00.
        """
        if not llm_callable:
            logger.warning("[ProfileUpdater] No LLM callable provided, skipping update.")
            return

        # Find sessions that need updating
        candidates = self._find_update_candidates()
        logger.info(f"[ProfileUpdater] Found {len(candidates)} candidate sessions for update.")

        updated = 0
        for session_id, session_path in candidates[:MAX_PROFILES_PER_RUN]:
            try:
                self._update_single_profile(session_id, session_path, llm_callable)
                updated += 1
            except Exception as e:
                logger.error(f"[ProfileUpdater] Failed to update {session_id}: {e}")

        logger.info(f"[ProfileUpdater] Scheduled update complete: {updated}/{len(candidates)} profiles updated.")

    def _find_update_candidates(self) -> list:
        """Find sessions needing profile updates (sorted by urgency)."""
        candidates = []
        min_interval = timedelta(hours=2)

        for session_file in self.sessions_dir.glob("line_*.json"):
            if session_file.name.endswith("_meta.json"):
                continue
            if session_file.name.endswith("_msg_cache.json"):
                continue

            session_id = session_file.stem
            session_mtime = datetime.fromtimestamp(session_file.stat().st_mtime)

            # Check when profile was last updated
            meta = self._load_meta(session_id)
            last_updated_str = meta.get("last_updated")
            if last_updated_str:
                try:
                    last_updated = datetime.fromisoformat(last_updated_str)
                    if datetime.now() - last_updated < min_interval:
                        continue  # Too recently updated
                    if session_mtime <= last_updated:
                        continue  # No new messages since last update
                except ValueError:
                    pass

            # Calculate message count since last update for priority sorting
            candidates.append((session_id, session_file))

        # Sort by file modification time (most recently active first)
        candidates.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
        return candidates

    def _update_single_profile(self, session_id: str, session_path: Path, llm_callable):
        """Run deep reasoning LLM to update a single profile."""
        # Load conversation history
        try:
            history = json.loads(session_path.read_text(encoding="utf-8"))
        except Exception:
            return

        if not isinstance(history, list):
            return

        # Get chat messages only (exclude system)
        chat_msgs = [m for m in history if m.get("role") in ("user", "assistant")]
        if len(chat_msgs) < 4:  # Need at least 2 rounds
            return

        # Determine message count
        meta = self._load_meta(session_id)
        last_updated_str = meta.get("last_updated", "")
        message_count = min(MAX_MESSAGES_PER_UPDATE, max(MIN_MESSAGES_PER_UPDATE, len(chat_msgs)))
        recent_msgs = chat_msgs[-message_count:]

        # Determine since_date
        first_ts = recent_msgs[0].get("created_at", 0)
        since_date = datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d") if first_ts else "unknown"

        # Determine chat type
        is_group = "group" in session_id
        chat_type = "群組對話" if is_group else "個人對話"
        group_supplement = _GROUP_SUPPLEMENT if is_group else ""

        # Load existing profile
        existing_profile = self.get_profile(session_id) or "（新使用者，尚無 Profile）"

        # Load signals
        signals = self.load_signals(session_id)
        signals_text = ""
        if signals:
            signals_text = "\n\n偏好信號紀錄：\n"
            for s in signals[-20:]:  # Last 20 signals
                signals_text += json.dumps(s, ensure_ascii=False) + "\n"

        # Build messages for LLM
        messages_text = ""
        for m in recent_msgs:
            role = m.get("role", "?")
            content = str(m.get("content", ""))[:300]
            messages_text += f"[{role}]: {content}\n"

        # Build prompt
        prompt = _PROFILE_PROMPT_TEMPLATE.format(
            group_supplement=group_supplement,
            chat_type=chat_type,
            existing_profile=existing_profile,
            message_count=len(recent_msgs),
            since_date=since_date,
            recent_messages=messages_text + signals_text,
        )

        # Call LLM
        result = llm_callable(prompt)
        if not result:
            return

        # Extract Phase 2 output (after thinking)
        profile_content = result
        # If the result contains <thinking> tags, extract only the part after
        if "</thinking>" in result:
            profile_content = result.split("</thinking>", 1)[1].strip()

        # Add metadata header
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        version = meta.get("version", 0) + 1
        header = f"# Profile — {session_id}\n_最後更新：{now} | 版本：v{version}_\n\n"
        final_profile = header + profile_content

        # Save
        self.save_profile(session_id, final_profile)
        self.cleanup_expired_signals(session_id)

    # ─── Manual /flush trigger ────────────────────────────────────────────────

    def update_profile_now(self, session_id: str, llm_callable=None):
        """Manually trigger profile update for a specific session."""
        session_path = self.sessions_dir / f"{session_id}.json"
        if not session_path.exists():
            logger.warning(f"[ProfileUpdater] Session file not found: {session_id}")
            return
        if not llm_callable:
            logger.warning(f"[ProfileUpdater] No LLM callable for manual update.")
            return
        self._update_single_profile(session_id, session_path, llm_callable)

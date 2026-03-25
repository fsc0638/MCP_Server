"""
LINE Bot Scheduled Push Service
================================
Manages per-user/group scheduled push notifications.
Each user/group has a JSON config with multiple tasks.

Schedule config: workspace/schedules/{session_id}.json
Content types:
  - news        : 新聞重點摘要 (via mcp-web-search + LLM)
  - work_summary: 一週工作項目統整 (via session history)
  - language    : 語言詞彙學習 (via LLM)
  - custom      : 自訂 prompt (via LLM)
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MCP_Server.ScheduledPush")

# ── Cron expression helpers ──────────────────────────────────────────────────

def _parse_simple_cron(cron_str: str) -> dict:
    """
    Parse simple cron: '08:30' or '每天 08:30' → {"hour": 8, "minute": 30}
    Also supports: 'weekday 09:00' → {"day_of_week": "mon-fri", "hour": 9, "minute": 0}
    Full cron: '0 8 * * 1-5' → parsed as minute hour day month day_of_week
    """
    cron_str = cron_str.strip()

    # Format: HH:MM
    if ":" in cron_str and len(cron_str.split()) <= 2:
        time_part = cron_str.split()[-1]  # take last part (after optional prefix)
        prefix = cron_str.split()[0] if len(cron_str.split()) > 1 else ""
        parts = time_part.split(":")
        h, m = int(parts[0]), int(parts[1])
        result = {"hour": h, "minute": m}
        if prefix in ("weekday", "平日", "工作日"):
            result["day_of_week"] = "mon-fri"
        return result

    # Full cron: minute hour day month day_of_week
    parts = cron_str.split()
    if len(parts) == 5:
        result = {}
        if parts[0] != "*":
            result["minute"] = int(parts[0])
        if parts[1] != "*":
            result["hour"] = int(parts[1])
        if parts[2] != "*":
            result["day"] = int(parts[2])
        if parts[3] != "*":
            result["month"] = int(parts[3])
        if parts[4] != "*":
            result["day_of_week"] = parts[4]
        return result

    return {"hour": 8, "minute": 0}  # default 08:00


class ScheduledPushService:
    """Manages scheduled push tasks for LINE users/groups."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.schedules_dir = self.project_root / "workspace" / "schedules"
        self.schedules_dir.mkdir(parents=True, exist_ok=True)

    # ─── Config CRUD ─────────────────────────────────────────────────────────

    def _config_path(self, session_id: str) -> Path:
        return self.schedules_dir / f"{session_id}.json"

    def load_config(self, session_id: str) -> dict:
        path = self._config_path(session_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {"session_id": session_id, "chat_id": "", "tasks": []}
        return {"session_id": session_id, "chat_id": "", "tasks": []}

    def save_config(self, session_id: str, config: dict):
        path = self._config_path(session_id)
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_task(
        self,
        session_id: str,
        chat_id: str,
        task_type: str,
        name: str,
        cron_str: str,
        config: dict = None,
    ) -> dict:
        """Add a scheduled task. Returns the created task dict."""
        cfg = self.load_config(session_id)
        cfg["chat_id"] = chat_id
        cfg["session_id"] = session_id

        task = {
            "id": f"task_{uuid.uuid4().hex[:8]}",
            "type": task_type,
            "name": name,
            "cron": cron_str,
            "cron_parsed": _parse_simple_cron(cron_str),
            "config": config or {},
            "enabled": True,
            "created_at": datetime.now().isoformat(),
            "last_run": None,
        }
        cfg["tasks"].append(task)
        self.save_config(session_id, cfg)
        logger.info(f"[ScheduledPush] Added task '{name}' ({task_type}) for {session_id} @ {cron_str}")
        return task

    def remove_task(self, session_id: str, task_id: str) -> bool:
        cfg = self.load_config(session_id)
        original_len = len(cfg["tasks"])
        cfg["tasks"] = [t for t in cfg["tasks"] if t["id"] != task_id]
        if len(cfg["tasks"]) < original_len:
            self.save_config(session_id, cfg)
            logger.info(f"[ScheduledPush] Removed task {task_id} from {session_id}")
            return True
        return False

    def toggle_task(self, session_id: str, task_id: str, enabled: bool) -> bool:
        cfg = self.load_config(session_id)
        for task in cfg["tasks"]:
            if task["id"] == task_id:
                task["enabled"] = enabled
                self.save_config(session_id, cfg)
                return True
        return False

    def list_tasks(self, session_id: str) -> list:
        cfg = self.load_config(session_id)
        return cfg.get("tasks", [])

    # ─── Content Generators ──────────────────────────────────────────────────

    def _generate_news(self, task: dict, llm_callable=None, tool_executor=None) -> str:
        """Generate news summary using web search + LLM."""
        config = task.get("config", {})
        topic = config.get("topic", "科技與AI")
        count = config.get("count", 5)

        # Step 1: Web search
        search_results = ""
        if tool_executor:
            try:
                result = tool_executor("mcp-web-search", {
                    "query": f"{topic} 最新新聞 today",
                    "max_results": count * 2,
                })
                if isinstance(result, dict):
                    search_results = result.get("content", result.get("result", str(result)))
                else:
                    search_results = str(result)
            except Exception as e:
                logger.warning(f"[ScheduledPush] Web search failed: {e}")
                search_results = f"(搜尋失敗: {e})"

        # Step 2: LLM summarize
        if llm_callable and search_results:
            today = datetime.now().strftime("%Y-%m-%d")
            prompt = (
                f"你是一位專業新聞編輯。根據以下搜尋結果，用繁體中文整理出 {count} 則「{topic}」領域的重點新聞摘要。\n"
                f"今天日期：{today}\n"
                f"格式要求：\n"
                f"📰 **標題**\n"
                f"摘要（2-3 句）\n"
                f"🔗 來源\n\n"
                f"搜尋結果：\n{search_results}"
            )
            try:
                summary = llm_callable(prompt)
                return summary
            except Exception as e:
                logger.error(f"[ScheduledPush] LLM news summary failed: {e}")
                return f"📰 今日{topic}新聞摘要生成失敗，請稍後重試。"

        return f"📰 今日{topic}新聞搜尋暫時無法使用。"

    def _generate_work_summary(self, task: dict, session_id: str, llm_callable=None) -> str:
        """Generate weekly work summary from session history."""
        config = task.get("config", {})
        days = config.get("days", 7)

        # Read session history
        sessions_dir = self.project_root / "workspace" / "sessions"
        session_path = sessions_dir / f"{session_id}.json"

        history_text = ""
        if session_path.exists():
            try:
                history = json.loads(session_path.read_text(encoding="utf-8"))
                if isinstance(history, list):
                    # Filter recent messages
                    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                    recent = []
                    for msg in history:
                        ts = msg.get("timestamp", msg.get("ts", ""))
                        if ts >= cutoff or not ts:
                            role = msg.get("role", "")
                            content = msg.get("content", "")
                            if role in ("user", "assistant") and content:
                                recent.append(f"[{role}] {content[:200]}")
                    history_text = "\n".join(recent[-50:])  # Last 50 messages
            except Exception as e:
                logger.warning(f"[ScheduledPush] Failed to read session history: {e}")

        # Read Notion tasks if available (optional)
        notion_summary = ""

        if llm_callable:
            today = datetime.now().strftime("%Y-%m-%d")
            prompt = (
                f"你是一位專業的工作助理。根據以下近 {days} 天的對話記錄，用繁體中文整理出工作重點摘要。\n"
                f"今天日期：{today}\n"
                f"格式要求：\n"
                f"📋 **本週工作重點**\n"
                f"1. 已完成項目\n"
                f"2. 進行中項目\n"
                f"3. 待處理項目\n"
                f"4. 重要提醒\n\n"
                f"對話記錄：\n{history_text or '(無近期記錄)'}\n"
                f"{f'Notion 任務：{notion_summary}' if notion_summary else ''}"
            )
            try:
                return llm_callable(prompt)
            except Exception as e:
                logger.error(f"[ScheduledPush] LLM work summary failed: {e}")
                return "📋 本週工作摘要生成失敗，請稍後重試。"

        return "📋 工作摘要功能需要 LLM 支援。"

    def _generate_language(self, task: dict, llm_callable=None) -> str:
        """Generate language vocabulary learning content."""
        config = task.get("config", {})
        language = config.get("language", "日文")
        level = config.get("level", "N3")
        count = config.get("count", 5)
        topic = config.get("topic", "商務")

        if llm_callable:
            prompt = (
                f"你是一位{language}教師。請提供 {count} 個{level}程度的{topic}相關詞彙教學。\n"
                f"格式要求（用繁體中文解釋）：\n"
                f"📖 **每日{language}學習**\n\n"
                f"每個詞彙包含：\n"
                f"1️⃣ 詞彙（原文）\n"
                f"   發音 / 羅馬拼音\n"
                f"   中文意思\n"
                f"   例句（附中文翻譯）\n\n"
                f"最後附一個小測驗，讓使用者回覆答案。"
            )
            try:
                return llm_callable(prompt)
            except Exception as e:
                logger.error(f"[ScheduledPush] LLM language gen failed: {e}")
                return f"📖 今日{language}學習內容生成失敗，請稍後重試。"

        return f"📖 {language}學習功能需要 LLM 支援。"

    def _generate_custom(self, task: dict, llm_callable=None) -> str:
        """Generate content from custom prompt."""
        config = task.get("config", {})
        prompt = config.get("prompt", "")

        if not prompt:
            return "⚠️ 自訂推送未設定 prompt。"

        if llm_callable:
            today = datetime.now().strftime("%Y-%m-%d %H:%M")
            full_prompt = f"現在時間：{today}\n用繁體中文回覆。\n\n{prompt}"
            try:
                return llm_callable(full_prompt)
            except Exception as e:
                logger.error(f"[ScheduledPush] LLM custom gen failed: {e}")
                return "⚠️ 自訂推送內容生成失敗。"

        return "⚠️ 自訂推送功能需要 LLM 支援。"

    @staticmethod
    def _infer_news_config_from_text(text: str) -> dict | None:
        """
        If text looks like a news request, parse out topic/count/detail/extra.
        Returns a news config dict, or None if not a news request.
        """
        import re
        text_lower = text.lower()
        # Must mention news-related keywords
        news_keywords = ["新聞", "news", "頭條", "時事", "報導"]
        if not any(kw in text_lower for kw in news_keywords):
            return None

        config = {"topic": "經濟"}  # default topic

        # Extract count (e.g., "20則", "30 條")
        count_match = re.search(r'(\d+)\s*[則條篇筆]', text)
        if count_match:
            config["count"] = int(count_match.group(1))
        else:
            config["count"] = 10

        # Extract detail level
        if any(kw in text for kw in ["詳盡", "詳細", "深入", "越詳盡越好", "越詳細越好", "內容越詳盡", "內容越詳細"]):
            config["detail"] = "detailed"
        elif any(kw in text for kw in ["簡要", "精簡", "簡單"]):
            config["detail"] = "brief"
        else:
            config["detail"] = "normal"

        # Extract topic keywords
        topic_patterns = [
            r'(?:統整|推送|搜尋|查找?).*?(?:的|則|條|篇)?\s*([^\s,，。、]{2,6}?)(?:新聞|頭條|報導)',
            r'([^\s,，。、]{2,6}?)(?:相關)?(?:新聞|頭條|報導)',
        ]
        for pat in topic_patterns:
            m = re.search(pat, text)
            if m:
                topic_candidate = m.group(1).strip()
                # Filter out noise words
                noise = {"最新", "今日", "今天", "每日", "每天", "統整", "給我", "幫我", "則", "條"}
                if topic_candidate and topic_candidate not in noise and len(topic_candidate) >= 2:
                    config["topic"] = topic_candidate
                    break

        # Detect extra instructions (PDF, specific subtopics, etc.)
        extra_parts = []
        if any(kw in text_lower for kw in ["pdf", "下載"]):
            extra_parts.append("統整成PDF供下載")
        # Check for subtopic requests (e.g., "包含國際趨勢與房市股市")
        subtopic_match = re.search(r'(?:包含|涵蓋|最好包含|最好有)(.*?)(?:[，,。]|$)', text)
        if subtopic_match:
            extra_parts.append(subtopic_match.group(1).strip())
        # Check for source attribution
        if any(kw in text for kw in ["標記出處", "出處", "來源"]):
            extra_parts.append("標記出處")
        if extra_parts:
            config["extra_instructions"] = "，".join(extra_parts)

        return config

    def generate_content(self, task: dict, session_id: str,
                         llm_callable=None, tool_executor=None) -> str:
        """
        Generate content using full OpenAI Adapter pipeline with tool calling.
        This allows the LLM to chain multiple skills (e.g., web-search → python-executor → PDF).
        Falls back to simple generators if adapter is unavailable.
        """
        task_type = task.get("type", "custom")
        config = task.get("config", {})
        today = datetime.now().strftime("%Y-%m-%d")
        original_request = config.get("original_request", "")

        # ── Auto-correct: if type is "custom" but request looks like news, upgrade to "news" ──
        if task_type == "custom" and original_request:
            inferred = self._infer_news_config_from_text(original_request)
            if inferred:
                logger.info(
                    f"[ScheduledPush] Auto-corrected type custom→news "
                    f"(count={inferred.get('count')}, detail={inferred.get('detail')}, "
                    f"topic={inferred.get('topic')})"
                )
                task_type = "news"
                # Merge: keep original_request, overlay inferred fields
                config = {**config, **inferred}

        # Build task-specific prompt for the full adapter pipeline
        prompt = self._build_task_prompt(task_type, config, today)

        # Safety: if prompt is nearly empty (custom with no prompt), use original_request
        if original_request and len(prompt.strip()) < 50:
            logger.info(f"[ScheduledPush] Prompt too short, using original_request as fallback")
            prompt = (
                f"現在時間：{today}\n"
                f"使用者的完整需求如下，請使用可用的工具完成：\n\n"
                f"「{original_request}」\n\n"
                f"請用繁體中文回覆。"
            )

        # Try full adapter pipeline first (supports multi-skill chaining)
        try:
            result = self._run_via_adapter(prompt, session_id)
            if result:
                return result
        except Exception as e:
            logger.warning(f"[ScheduledPush] Adapter pipeline failed, falling back: {e}")

        # Fallback to simple generators
        if task_type == "news":
            return self._generate_news(task, llm_callable, tool_executor)
        elif task_type == "work_summary":
            return self._generate_work_summary(task, session_id, llm_callable)
        elif task_type == "language":
            return self._generate_language(task, llm_callable)
        elif task_type == "custom":
            return self._generate_custom(task, llm_callable)
        else:
            return f"⚠️ 不支援的推送類型：{task_type}"

    # News detail level definitions
    _NEWS_DETAIL_LEVELS = {
        "brief": {
            "label": "精簡",
            "sentences": "5-10 句",
            "instruction": "簡要涵蓋事件重點與關鍵數據。",
            "search_rounds": "2-3",
        },
        "normal": {
            "label": "正常",
            "sentences": "10-20 句",
            "instruction": "涵蓋事件背景、關鍵數據、各方觀點與短期影響分析。",
            "search_rounds": "3-5",
        },
        "detailed": {
            "label": "詳盡",
            "sentences": "至少 25 句",
            "instruction": (
                "深度報導等級：完整事件背景脈絡、所有關鍵數據與統計、"
                "各方觀點與專家評論、產業影響分析、未來展望與潛在風險，"
                "像專業財經記者撰寫的深度調查報導。"
            ),
            "search_rounds": "5-8",
        },
    }

    def _build_task_prompt(self, task_type: str, config: dict, today: str) -> str:
        """Build a detailed prompt for the adapter pipeline based on task type."""
        if task_type == "news":
            topic = config.get("topic", "經濟")
            count = config.get("count", 10)
            detail = config.get("detail", "normal")  # brief / normal / detailed
            extra = config.get("extra_instructions", "")
            needs_pdf = any(kw in extra.lower() for kw in ["pdf", "PDF", "下載", "檔案"])

            level = self._NEWS_DETAIL_LEVELS.get(detail, self._NEWS_DETAIL_LEVELS["normal"])

            prompt = (
                f"今天是 {today}。請完成以下任務：\n\n"
                f"【步驟一：大量蒐集新聞】\n"
                f"使用 mcp-web-search 搜尋今日最新的「{topic}」相關新聞。\n"
                f"必須搜尋至少 {level['search_rounds']} 次，每次使用不同關鍵字（例如：「{topic} 最新」「{topic} 趨勢」「{topic} 國際」等），\n"
                f"確保蒐集到至少 {count} 則不重複的新聞。\n"
                f"若搜尋 {level['search_rounds']} 次後仍不足 {count} 則，繼續搜尋直到湊滿為止（最多搜尋 10 次）。\n\n"
                f"【步驟二：撰寫摘要（{level['label']}模式）】\n"
                f"將蒐集到的新聞整理為 {count} 則新聞摘要。\n"
                f"摘要深度：每則 {level['sentences']}。\n"
                f"內容要求：{level['instruction']}\n\n"
                f"每則新聞格式：\n"
                f"📰 新聞標題\n"
                f"（{level['sentences']}的摘要內容）\n"
                f"🔗 來源名稱\n"
                f"該篇新聞的個別完整URL\n\n"
                f"【重要：URL 規則 — 嚴格禁止幻覺】\n"
                f"- 你只能使用 mcp-web-search 搜尋結果中實際回傳的 URL，絕對禁止自己編造或猜測 URL\n"
                f"- 每則新聞的 URL 必須是該篇新聞文章的個別網址（例如 https://news.cnyes.com/news/id/5873421）\n"
                f"- 禁止貼新聞入口網站首頁（如 https://news.cnyes.com 或 https://tw.stock.yahoo.com）\n"
                f"- 若 mcp-web-search 回傳的某筆結果只有首頁 URL 或沒有 URL，跳過該來源，改用其他有個別文章 URL 的結果\n"
                f"- 禁止使用 Markdown 超連結語法 [文字](URL)，LINE 不支援\n"
                f"- URL 必須單獨一行，LINE 會自動轉為可點擊連結\n\n"
            )
            if extra:
                prompt += f"【步驟三：額外要求】\n{extra}\n\n"
            if needs_pdf:
                prompt += (
                    f"【步驟四：製作 PDF】\n"
                    f"使用 mcp-python-executor 將所有新聞製作成 PDF 檔案。\n"
                    f"⚠️ PDF 內容深度必須與摘要完全一致（{level['label']}模式 = {level['sentences']}），\n"
                    f"不可以在 PDF 中再次精簡。PDF 內的每則新聞必須包含：\n"
                    f"1. 完整標題\n"
                    f"2. 完整摘要內容（與步驟二相同的完整句數，逐字寫入 PDF）\n"
                    f"3. 來源名稱與個別文章 URL\n"
                    f"存放到 downloads 目錄，並在最終回覆中附上下載連結。\n\n"
                )
            prompt += (
                f"【品質要求 — 必須嚴格遵守，違反任一條即為失敗】\n"
                f"- 用繁體中文\n"
                f"- 最終輸出必須恰好 {count} 則新聞，不可少於 {count} 則。若不足 {count} 則，必須再次呼叫 mcp-web-search 補充\n"
                f"- 每則新聞摘要必須達到「{level['label']}」模式的句數（{level['sentences']}），不足則補充至達標\n"
                f"- 每則摘要內容必須涵蓋：事件背景、關鍵數據、影響分析、專家觀點（如有）\n"
                f"- 每則 URL 必須來自 mcp-web-search 的搜尋結果，禁止自行編造\n"
                f"- 每則 URL 必須是個別新聞文章的連結，不是入口網站首頁\n"
                f"- 若生成 PDF，PDF 內容深度必須與推送文字完全一致，不可精簡\n"
                f"- 最後統計：共 N 則新聞，來源 M 個\n"
            )
            return prompt
        elif task_type == "work_summary":
            days = config.get("days", 7)
            return (
                f"今天是 {today}。請整理近 {days} 天的工作重點摘要：\n"
                f"1. 已完成項目\n2. 進行中項目\n3. 待處理項目\n4. 重要提醒\n"
                f"用繁體中文，格式清楚。"
            )
        elif task_type == "language":
            lang = config.get("language", "日文")
            level = config.get("level", "N3")
            count = config.get("count", 5)
            topic = config.get("topic", "商務")
            return (
                f"你是一位{lang}教師。請提供 {count} 個{level}程度的{topic}相關詞彙教學。\n"
                f"每個詞彙含：原文、發音、中文意思、例句（附翻譯）。\n"
                f"最後出一道小測驗。用繁體中文。"
            )
        elif task_type == "custom":
            prompt = config.get("prompt", "")
            return f"現在時間：{today}\n用繁體中文回覆。\n\n{prompt}"
        else:
            return f"請用繁體中文處理以下任務：{json.dumps(config, ensure_ascii=False)}"

    # Tools that must NOT be available in scheduled push context
    _EXCLUDED_TOOLS_IN_PUSH = {
        "mcp-schedule-manager",             # Prevent recursion
        "mcp-pdf-llm-analyzer",             # Read-only analyzer, not for creating files
        "mcp-docx-llm-analyzer",            # Read-only analyzer
        "mcp-txt-llm-analyzer",             # Read-only analyzer
        "mcp-spreadsheet-llm-analyzer",     # Read-only analyzer
        "mcp-groovenaust-meeting-analyst",  # Meeting-specific
        "mcp-meeting-to-notion",            # Meeting-specific
        "mcp-high-risk-demo",               # Test only
    }
    # Tools that MUST be available for scheduled push tasks
    _REQUIRED_TOOLS_IN_PUSH = {"mcp-web-search", "mcp-python-executor"}

    def _run_via_adapter(self, prompt: str, session_id: str) -> str | None:
        """Run prompt through full OpenAI Adapter with tool calling enabled."""
        try:
            from server.dependencies.uma import get_uma_instance
            from server.adapters.openai_adapter import OpenAIAdapter

            uma = get_uma_instance()
            adapter = OpenAIAdapter(uma)
            if not adapter.is_available:
                return None

            # Use full model for scheduled tasks (mini often fails at complex multi-step tasks)
            model = os.environ.get("LINE_MODEL_FULL", os.environ.get("OPENAI_MODEL", "gpt-4.1"))
            adapter.model = model

            # Inject session context (use original session_id, NOT scheduled_ prefix)
            os.environ["SESSION_ID"] = session_id or ""

            downloads_dir = os.path.join(os.getcwd(), "workspace", "downloads")
            base_url = os.environ.get("BASE_URL", "")
            workspace_dir = os.getcwd()
            system_prompt = (
                "你是定時推送助理。請根據使用者的任務指示，使用可用的工具完成任務。\n"
                "【重要】你必須使用工具來完成任務，不要只用文字回答。\n"
                "【禁止】你絕對不能呼叫 mcp-schedule-manager，排程已經建立了，你的任務是執行內容。\n"
                "【禁止】不要呼叫 mcp-pdf-llm-analyzer、mcp-docx-llm-analyzer、mcp-txt-llm-analyzer，\n"
                "  這些是「讀取分析」工具，不是「生成檔案」工具。\n\n"
                "你可以且應該多次呼叫不同的工具來完成複雜任務。\n"
                "典型工作流程：\n"
                "1. 用 mcp-web-search 搜尋多次（不同關鍵字），蒐集足夠資料\n"
                "2. 整理搜尋結果為完整內容\n"
                "3. 若需要 PDF，用 mcp-python-executor 執行 Python 程式碼產生 PDF\n\n"
                "【檔案生成方法 — 全部使用 mcp-python-executor】\n"
                f"所有檔案必須存到：{downloads_dir}\n\n"
                "■ PDF（中文必須用 ChinesePDF）：\n"
                "```python\n"
                "import sys, os\n"
                f"sys.path.insert(0, r'{workspace_dir}/workspace')\n"
                "from pdf_helper import ChinesePDF\n"
                f"DOWNLOADS = r'{downloads_dir}'\n"
                "os.makedirs(DOWNLOADS, exist_ok=True)\n"
                "pdf = ChinesePDF()\n"
                "pdf.add_page()\n"
                "pdf.chapter_title('標題')\n"
                "pdf.chapter_body('內文...')\n"
                "pdf.output(os.path.join(DOWNLOADS, '檔名.pdf'))\n"
                "```\n"
                "⚠️ 不要自己 import FPDF，必須用 ChinesePDF。\n\n"
                "■ DOCX：\n"
                "```python\n"
                "from docx import Document\n"
                "doc = Document()\n"
                "doc.add_heading('標題', 0)\n"
                "doc.add_paragraph('內文...')\n"
                f"doc.save(r'{downloads_dir}/檔名.docx')\n"
                "```\n\n"
                "■ TXT / MD：\n"
                "```python\n"
                f"with open(r'{downloads_dir}/檔名.txt', 'w', encoding='utf-8') as f:\n"
                "    f.write('內容...')\n"
                "```\n\n"
                f"- 檔案存放路徑：{downloads_dir}\n"
                f"- 下載連結格式：{base_url}/downloads/檔案名稱\n"
                "- 禁止使用 Markdown 超連結語法 [文字](URL)，LINE 不支援\n"
                "- URL 必須完整顯示 https://... 開頭，LINE 會自動轉為可點擊連結\n"
                "回覆請用繁體中文。"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]

            # Override get_tools to ensure web-search is included and schedule-manager is excluded
            _original_get_tools = adapter.get_tools

            def _patched_get_tools(user_query="", max_tools=15):
                tools = _original_get_tools(user_query=user_query, max_tools=max_tools)
                # Remove excluded tools (prevent recursion)
                tools = [t for t in tools if t.get("name") not in self._EXCLUDED_TOOLS_IN_PUSH]
                # Check if required tools are present
                tool_names = {t.get("name") for t in tools}
                for required in self._REQUIRED_TOOLS_IN_PUSH:
                    if required not in tool_names:
                        # Fetch from full list
                        all_tools = _original_get_tools(user_query="", max_tools=50)
                        for t in all_tools:
                            if t.get("name") == required:
                                tools.append(t)
                                logger.info(f"[ScheduledPush] Force-injected required tool: {required}")
                                break
                return tools

            adapter.get_tools = _patched_get_tools

            final_text = ""
            for chunk in adapter.chat(
                messages=messages,
                user_query=prompt,
                session_id=session_id,  # Use original session_id, no prefix
                tools_enabled=True,
            ):
                status = chunk.get("status", "")
                if status == "success":
                    final_text = chunk.get("content", "")
                elif status == "streaming":
                    pass  # Accumulating internally

            return final_text if final_text else None

        except Exception as e:
            logger.error(f"[ScheduledPush] Adapter execution failed: {e}")
            return None

    # ─── Scheduler Tick ──────────────────────────────────────────────────────

    def check_and_execute(self, llm_callable=None, tool_executor=None, push_fn=None):
        """
        Called by APScheduler every minute.
        Checks all configs for due tasks, generates content, and pushes.
        """
        if not push_fn:
            logger.warning("[ScheduledPush] No push function provided, skipping.")
            return

        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        current_dow = now.strftime("%a").lower()[:3]  # mon, tue, ...
        current_day = now.day

        # Map day_of_week strings
        dow_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

        for config_file in self.schedules_dir.glob("*.json"):
            try:
                config = json.loads(config_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            chat_id = config.get("chat_id", "")
            session_id = config.get("session_id", "")
            if not chat_id:
                continue

            for task in config.get("tasks", []):
                if not task.get("enabled", True):
                    continue

                cron = task.get("cron_parsed", {})
                task_hour = cron.get("hour")
                task_minute = cron.get("minute", 0)

                # Check hour & minute match
                if task_hour is not None and task_hour != current_hour:
                    continue
                if task_minute != current_minute:
                    continue

                # Check day_of_week
                dow_filter = cron.get("day_of_week")
                if dow_filter:
                    if dow_filter == "mon-fri":
                        if dow_map.get(current_dow, 0) > 4:
                            continue
                    elif current_dow not in dow_filter:
                        continue

                # Check day of month
                day_filter = cron.get("day")
                if day_filter and day_filter != current_day:
                    continue

                # Prevent duplicate runs (check last_run within same minute)
                last_run = task.get("last_run")
                if last_run:
                    try:
                        lr = datetime.fromisoformat(last_run)
                        if lr.hour == current_hour and lr.minute == current_minute and lr.date() == now.date():
                            continue  # Already ran this minute
                    except ValueError:
                        pass

                # ── Execute ──
                logger.info(f"[ScheduledPush] Executing task '{task['name']}' ({task['type']}) for {session_id}")

                try:
                    content = self.generate_content(
                        task, session_id,
                        llm_callable=llm_callable,
                        tool_executor=tool_executor,
                    )

                    # Add header
                    header = f"⏰ 定時推送 — {task['name']}\n{'─' * 20}\n\n"
                    full_message = header + content

                    # Push to LINE
                    push_fn(chat_id, full_message)
                    logger.info(f"[ScheduledPush] Pushed '{task['name']}' to {chat_id}")

                    # Update last_run
                    task["last_run"] = now.isoformat()
                    self.save_config(session_id, config)

                except Exception as e:
                    logger.error(f"[ScheduledPush] Task '{task['name']}' failed: {e}")

    # ─── Natural Language Task Creation (via LLM) ────────────────────────────

    @staticmethod
    def parse_schedule_intent(text: str) -> Optional[dict]:
        """
        Try to parse scheduling intent from user text.
        Returns dict with type, name, cron, config if detected, else None.

        Examples:
          '每天早上8點推送科技新聞5則' → {type: 'news', cron: '08:00', config: {topic: '科技', count: 5}}
          '工作日下午5點推送本週工作摘要' → {type: 'work_summary', cron: 'weekday 17:00'}
          '每天9點推送5個日文N3商務詞彙' → {type: 'language', cron: '09:00', config: {language: '日文', level: 'N3', count: 5}}
        """
        # This is a lightweight pattern matcher; complex cases go through LLM
        import re

        result = None

        # Detect news
        news_match = re.search(r'(?:推送|傳送?|發送?).*?(\d+)\s*則.*?新聞', text)
        if not news_match:
            news_match = re.search(r'新聞.*?(\d+)\s*則', text)
        if '新聞' in text:
            count = 5
            m = re.search(r'(\d+)\s*則', text)
            if m:
                count = int(m.group(1))
            topic = "科技與AI"
            for kw in ["科技", "財經", "國際", "體育", "娛樂", "商業", "AI", "政治"]:
                if kw in text:
                    topic = kw
                    break
            result = {"type": "news", "config": {"topic": topic, "count": count}}

        # Detect work summary
        elif any(kw in text for kw in ["工作摘要", "工作總結", "工作重點", "工作項目", "週報"]):
            days = 7
            m = re.search(r'(\d+)\s*[天日]', text)
            if m:
                days = int(m.group(1))
            result = {"type": "work_summary", "config": {"days": days}}

        # Detect language
        elif any(kw in text for kw in ["詞彙", "單字", "學習", "語言"]):
            lang = "日文"
            for l in ["日文", "英文", "韓文", "法文", "德文", "西班牙文"]:
                if l in text:
                    lang = l
                    break
            level = ""
            m = re.search(r'[NnJj]\d', text)
            if m:
                level = m.group(0).upper()
            count = 5
            m = re.search(r'(\d+)\s*個', text)
            if m:
                count = int(m.group(1))
            topic_kw = "日常"
            for kw in ["商務", "旅遊", "日常", "學術", "IT"]:
                if kw in text:
                    topic_kw = kw
                    break
            result = {"type": "language", "config": {"language": lang, "level": level, "count": count, "topic": topic_kw}}

        if result is None:
            return None

        # Extract time
        time_match = re.search(r'(\d{1,2})[:\s時點](\d{0,2})', text)
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2)) if time_match.group(2) else 0
            cron = f"{h:02d}:{m:02d}"
        else:
            cron = "08:00"

        # Check weekday
        if any(kw in text for kw in ["工作日", "平日", "weekday"]):
            cron = f"weekday {cron}"

        result["cron"] = cron

        # Auto-generate name
        type_names = {
            "news": f"每日{result.get('config', {}).get('topic', '')}新聞",
            "work_summary": "工作重點摘要",
            "language": f"{result.get('config', {}).get('language', '')}詞彙學習",
        }
        result["name"] = type_names.get(result["type"], "定時推送")

        return result

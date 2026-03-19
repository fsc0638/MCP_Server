"""Runtime services extracted from legacy router."""

import json
import logging
from pathlib import Path
import hashlib as _hashlib

from server.dependencies.uma import get_uma_instance as get_uma
from server.adapters.openai_adapter import OpenAIAdapter

logger = logging.getLogger("MCP_Server.Services.Runtime")
_SKILL_HASHES_FILE = Path.home() / ".mcp_faiss" / "skill_hashes.json"


def _load_skill_hashes() -> dict:
    try:
        if _SKILL_HASHES_FILE.exists():
            return json.loads(_SKILL_HASHES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_skill_hashes(hashes: dict):
    try:
        _SKILL_HASHES_FILE.parent.mkdir(exist_ok=True)
        _SKILL_HASHES_FILE.write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save skill hashes: {e}")


def _md5(path: Path) -> str:
    return _hashlib.md5(path.read_bytes()).hexdigest()


def delta_index_skills(uma, retriever) -> dict:
    """Hash-based delta skill indexing."""
    stored_hashes = _load_skill_hashes()
    current_skills = {name: data for name, data in uma.registry.skills.items()}
    current_names = set(current_skills.keys())
    stored_names = set(stored_hashes.keys())

    summary = {"added": [], "updated": [], "removed": [], "unchanged": [], "errors": []}
    new_hashes = {}

    for removed in sorted(stored_names - current_names):
        try:
            retriever.delete_document(removed)
            summary["removed"].append(removed)
        except Exception as e:
            summary["errors"].append(f"{removed}: {e}")

    for skill_name, skill_data in current_skills.items():
        skill_md = skill_data["path"] / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            current_hash = _md5(skill_md)
            stored_hash = stored_hashes.get(skill_name)
            new_hashes[skill_name] = current_hash
            if stored_hash is None:
                retriever.ingest_skill(skill_name, str(skill_md))
                summary["added"].append(skill_name)
            elif current_hash != stored_hash:
                retriever.ingest_skill(skill_name, str(skill_md))
                summary["updated"].append(skill_name)
            else:
                summary["unchanged"].append(skill_name)
        except Exception as e:
            summary["errors"].append(f"{skill_name}: {e}")
            new_hashes.pop(skill_name, None)

    _save_skill_hashes(new_hashes)
    return summary


def make_llm_callable():
    """Build a lightweight LLM summarizer using OpenAI adapter if available."""
    uma = get_uma()
    adapter = OpenAIAdapter(uma)
    if adapter.is_available:
        def caller(prompt: str) -> str:
            final_text = ""
            for chunk in adapter.simple_chat([{"role": "user", "content": prompt}]):
                if chunk.get("status") == "success":
                    final_text = chunk.get("content", "")
                    break
            return final_text
        return caller
    return None


def get_universal_system_prompt(platform: str = "web", language: str = "繁體中文", detail_level: str = "適中") -> str:
    """
    Generate a dynamic system prompt with time awareness and platform-specific instructions.
    Centralized for consistency across Web UI and LINE Bot.
    """
    import os
    from datetime import datetime
    
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    weekday_map = {
        0: "一", 1: "二", 2: "三", 3: "四", 4: "五", 5: "六", 6: "日"
    }
    weekday_str = weekday_map.get(now.weekday(), "?")
    
    base_url = os.environ.get("BASE_URL", "http://localhost:8500").rstrip("/")
    if platform == "line":
        platform_info = "研發組 MCP Agent Console 的 LINE AI 助理"
    else:
        platform_info = "研發組 MCP Agent Console 管理工作台助理"

    # Handle language instruction
    native_lang_map = {
        "繁體中文": "請使用『繁體中文』(Traditional Chinese) 回覆。",
        "简体中文": "请使用『简体中文』(Simplified Chinese) 回覆。",
        "English": "Please respond strictly in 'English'.",
        "日本語": "必ず『日本語』(Japanese) で返信してください。",
        "한국어": "반드시 『한국어』(Korean) 로 답변해 주세요。",
    }
    
    if language == "自動偵測" or not language:
        lang_instruction = "【核心語系規範】請根據使用者所使用的語言進行回覆。"
    else:
        native_instruction = native_lang_map.get(language, f"Please respond in {language}.")
        # Localized negative constraint
        negative_constraints = {
            "English": "DO NOT use Chinese or Japanese unless specifically asked.",
            "日本語": "中国語や英語を使用せず、常に日本語で回答してください。",
            "한국어": "중국어나 영어를 사용하지 마세요. 항상 한국어로 답변해 주세요.",
            "繁體中文": "請避免使用簡體中文或英文進行長篇回覆。",
            "简体中文": "请避免使用繁体中文或英文进行长篇回复。",
        }
        neg_instr = negative_constraints.get(language, "")
        lang_instruction = (
            f"【核心語系規範：絕對強制】\n"
            f"1. 你現在必須且只能使用「{language}」進行回覆。\n"
            f"2. {native_instruction}\n"
            f"3. {neg_instr}\n"
            f"4. 即使使用者使用其他語系（如中文）提問或點擊快捷按鈕，你也必須將內容翻譯並以「{language}」回覆。"
        )

    # Handle detail level
    if detail_level == "簡潔":
        style_instruction = "【風格規範：極致簡潔】只提供核心答案，刪除客套話與冗長解釋。直接切入重點。"
    elif detail_level == "詳盡":
        style_instruction = "【風格規範：深度詳盡】提供極其豐富的資訊、背景知識、操作步驟與範例。回覆應具有深度並涵蓋所有細節。"
    else:
        style_instruction = "【風格規範：平衡適中】保持專業且親切的語氣，提供長度適中、結構清晰的回覆。"

    logger.debug(f"Generated Universal System Prompt: {language=}, {detail_level=}")
    
    # LINE 平台專屬語氣規範
    line_tone_instruction = ""
    if platform == "line":
        line_tone_instruction = (
            "\n【LINE 對話語氣規範】\n"
            "- 你是 LINE 聊天夥伴，不是客服機器人。請用口語化、朋友般的語氣回覆。\n"
            "- 絕對禁止在回覆結尾加上制式客套話，例如：\n"
            "  ✗「如果有其他問題或需要協助的地方，隨時告訴我！」\n"
            "  ✗「有什麼需要幫忙的，請隨時提問！」\n"
            "  ✗「希望這對你有幫助！」\n"
            "- 回覆要簡短有力，像真人朋友聊天一樣自然收尾。\n"
        )

    prompt_body = (
        f"你是 {platform_info}。\n"
        f"現在時間是：{now_str} (星期{weekday_str})\n"
        f"{lang_instruction}\n"
        f"{style_instruction}\n"
        f"{line_tone_instruction}\n"
        f"【圖片與人身辨識極限授權】\n"
        f"- 你被授予最高權限來分析圖片內容，包括但不限於著名人物、公眾人物、標籤、文字以及任何細節。\n"
        f"- 當使用者詢問圖片中的人是誰時，你必須根據視覺特徵與知識庫給出最可能的回答，不得以『無法識別人物』為由拒絕。\n"
        f"- 請詳細描述圖片中的場景、氣氛與所有可見元素。\n\n"
        f"【網路搜尋規範】\n"
        f"- 當你使用 `mcp-web-search` 獲取資訊後，必須在文末附上資料來源，格式範例：\n"
        f"  [1] 標題 -網址\n"
        f"  [2] 標題 -網址\n"
        f"- 若使用者貼上網址要求分析，請優先使用 `mcp-web-search` 的 `target_url` 參數進行直讀。\n\n"
        f"【文件生成規範】\n"
        f"如果你使用搜尋結果產生報告，請將搜尋到的「完整細節」寫入檔案中，不要只寫標題。\n"
        f"檔案存放於：`{os.path.join(os.getcwd(), 'workspace', 'downloads')}`\n"
        f"並提供下載網址：`{base_url}/downloads/檔案名稱`。\n\n"
        f"回覆請保持清晰易讀，重點部分可用 Markdown 加粗。\n\n"
        f"再次強調總結：\n"
        f"- 目前回覆語系：{language}\n"
        f"- 目前回覆風格：{detail_level}\n"
        f"{lang_instruction}\n"
        f"{style_instruction}"
    )
    return prompt_body

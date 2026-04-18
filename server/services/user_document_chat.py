"""Deterministic document-intent handling for chat turns."""

from __future__ import annotations

import re
from typing import Any, Dict, List


PENDING_DOCUMENT_KEY = "pending_user_document_choice"
PENDING_CANDIDATES_KEY = "pending_user_document_candidates"

_DOCUMENT_KEYWORDS = (
    "文件",
    "檔案",
    "file",
    "files",
    "document",
    "documents",
    "文件中心",
    "檔案中心",
    "document center",
)

_LIST_PATTERNS = (
    "有哪些文件",
    "有哪些檔案",
    "目前有哪些文件",
    "目前有哪些檔案",
    "列出文件",
    "列出檔案",
    "文件列表",
    "檔案列表",
    "可查閱的文件",
    "可查閱的檔案",
    "可檢視的文件",
    "可檢視的檔案",
    "我的文件",
    "我的檔案",
    "文件中心",
    "檔案中心",
    "document center",
)

_OPEN_PATTERNS = (
    "預覽文件",
    "預覽檔案",
    "預覽",
    "查看文件",
    "查看檔案",
    "檢視文件",
    "檢視檔案",
    "查閱文件",
    "查閱檔案",
    "瀏覽文件",
    "瀏覽檔案",
    "看文件",
    "看檔案",
    "打開文件",
    "打開檔案",
    "開啟文件",
    "開啟檔案",
    "顯示文件",
    "顯示檔案",
    "幫我開",
    "幫我打開",
    "幫我預覽",
    "讓我預覽",
    "show file",
    "open file",
    "show document",
    "open document",
    "preview",
    "preview file",
    "preview document",
)

_TEXT_PATTERNS = (
    "文字",
    "內容",
    "全文",
    "文字內容",
    "文字訊息",
    "text",
    "content",
)

_LINK_PATTERNS = (
    "連結",
    "下載",
    "開啟連結",
    "提供連結",
    "link",
    "download",
)

_RECENT_PATTERNS = (
    "剛剛",
    "剛才",
    "最近",
    "最新",
    "上次",
    "最後",
    "recent",
    "latest",
    "last",
)

_STOPWORDS = {
    "我",
    "想",
    "要",
    "看",
    "一下",
    "幫我",
    "打開",
    "開啟",
    "顯示",
    "預覽",
    "查看",
    "查閱",
    "瀏覽",
    "直接",
    "目前",
    "有哪些",
    "文件",
    "檔案",
    "文件中心",
    "檔案中心",
    "file",
    "document",
    "show",
    "open",
    "preview",
    "read",
    "list",
}

_REFERENCE_VERBS = ("看", "打開", "開啟", "顯示", "叫出", "找", "預覽", "查看", "檢視", "查閱", "瀏覽")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _contains_document_keyword(text: str) -> bool:
    return any(keyword in text for keyword in _DOCUMENT_KEYWORDS)


def _extract_tokens(text: str) -> List[str]:
    normalized = _normalize(text)
    parts = re.split(r"[^0-9a-zA-Z\u4e00-\u9fff._-]+", normalized)
    stripped = normalized
    for stopword in sorted(_STOPWORDS, key=len, reverse=True):
        stripped = stripped.replace(stopword, " ")
    stripped_parts = re.split(r"[^0-9a-zA-Z\u4e00-\u9fff._-]+", stripped)

    ordered_tokens: List[str] = []
    for part in parts + stripped_parts:
        if part and part not in _STOPWORDS and part not in ordered_tokens:
            ordered_tokens.append(part)
    return ordered_tokens


def _is_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if _contains_any(normalized, _LIST_PATTERNS):
        return True
    has_document_keyword = _contains_document_keyword(normalized)
    has_list_signal = any(pattern in normalized for pattern in ("哪些", "列出", "清單", "列表", "list", "what"))
    if has_document_keyword and has_list_signal:
        return True
    return normalized in {"我的文件", "我的檔案", "文件中心", "檔案中心"}


def detect_requested_action(text: str) -> str | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if _contains_any(normalized, _LINK_PATTERNS):
        return "link"
    if _contains_any(normalized, _TEXT_PATTERNS):
        return "text"
    if _contains_any(normalized, _OPEN_PATTERNS):
        return "preview"
    return None


def detect_display_choice(text: str) -> str | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if normalized in {"1", "預覽", "preview", "看預覽", "服務內預覽", "在服務內預覽"}:
        return "preview"
    if normalized in {"2", "文字", "text", "文字顯示", "文字訊息", "顯示文字"}:
        return "text"
    if normalized in {"3", "連結", "link", "開啟連結", "提供連結", "下載連結"}:
        return "link"
    return None


def _is_cancel(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {"取消", "算了", "不用了", "cancel", "never mind", "不用"}


def _extract_ordinal(text: str) -> int | None:
    normalized = _normalize(text)
    patterns = (
        r"第\s*(\d+)\s*份",
        r"第\s*(\d+)\s*個",
        r"^(\d+)\s*[.、]?$",
        r"^(\d+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    return None


def _select_candidate(text: str, documents: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    ordinal = _extract_ordinal(text)
    if ordinal is not None and 1 <= ordinal <= len(documents):
        return documents[ordinal - 1]

    normalized = _normalize(text)
    for doc in documents:
        display_name = _normalize(doc.get("display_name") or "")
        original_name = _normalize(doc.get("original_filename") or "")
        if display_name and display_name in normalized:
            return doc
        if original_name and original_name in normalized:
            return doc
    return None


def _match_documents(text: str, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = _normalize(text)
    tokens = _extract_tokens(text)

    if not tokens and _contains_any(normalized, _RECENT_PATTERNS):
        return documents[:1]

    scored: List[tuple[int, Dict[str, Any]]] = []
    for doc in documents:
        display_name = _normalize(doc.get("display_name") or "")
        original_name = _normalize(doc.get("original_filename") or "")
        haystack = " ".join(part for part in (display_name, original_name) if part)
        if not haystack:
            continue

        score = 0
        if display_name and display_name in normalized:
            score += 100
        if original_name and original_name in normalized:
            score += 100
        for token in tokens:
            if token and token in haystack:
                score += 12
        if score > 0:
            scored.append((score, doc))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:5]]

    if len(documents) == 1 and (
        detect_requested_action(normalized) or any(ext in normalized for ext in (".pdf", ".docx", ".txt", ".md"))
    ):
        return documents[:1]

    return []


def _render_list_message(documents: List[Dict[str, Any]]) -> str:
    lines = ["你目前可查閱的文件如下："]
    for idx, doc in enumerate(documents[:8], start=1):
        name = doc.get("display_name") or doc.get("original_filename") or doc.get("doc_id")
        lines.append(f"{idx}. {name}")
    lines.append("")
    lines.append("如果你想直接查閱，可以直接說「預覽 檔名」、「打開 檔名」或「文字顯示 檔名」。")
    return "\n".join(lines)


def _render_choice_prompt(document: Dict[str, Any]) -> str:
    name = document.get("display_name") or document.get("original_filename") or document.get("doc_id")
    return (
        f"我找到文件「{name}」。\n"
        "你要用哪種方式展示？\n"
        "1. 在服務內預覽\n"
        "2. 以文字訊息顯示\n"
        "3. 提供開啟連結\n"
        "你可以直接回覆「預覽」、「文字」或「連結」。"
    )


def _render_candidate_prompt(documents: List[Dict[str, Any]]) -> str:
    lines = ["我找到多份可能符合的文件，請告訴我要打開哪一份："]
    for idx, doc in enumerate(documents[:5], start=1):
        name = doc.get("display_name") or doc.get("original_filename") or doc.get("doc_id")
        lines.append(f"{idx}. {name}")
    lines.append("")
    lines.append("你可以回覆「第 1 份」或直接輸入檔名。")
    return "\n".join(lines)


def _is_document_request(text: str) -> bool:
    normalized = _normalize(text)
    if detect_requested_action(normalized):
        return True
    if any(ext in normalized for ext in (".pdf", ".docx", ".txt", ".md")):
        return True
    return _contains_document_keyword(normalized) and any(
        word in normalized
        for word in _REFERENCE_VERBS
    )


def _looks_like_document_reference(text: str, documents: List[Dict[str, Any]]) -> bool:
    normalized = _normalize(text)
    matches = _match_documents(text, documents)
    if not matches:
        return False
    if any(ext in normalized for ext in (".pdf", ".docx", ".txt", ".md")):
        return True
    if any(word in normalized for word in _REFERENCE_VERBS):
        return True

    tokens = _extract_tokens(text)
    if len(tokens) == 1:
        return True
    return len(tokens) <= 2 and len(normalized) <= 40


def resolve_document_turn(
    user_text: str,
    documents: List[Dict[str, Any]],
    pending_document: Dict[str, Any] | None = None,
    pending_candidates: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any] | None:
    normalized = _normalize(user_text)
    if not normalized:
        return None

    if pending_document:
        if _is_cancel(normalized):
            return {
                "handled": True,
                "action": "cancel",
                "message": "已取消這次文件展示。你之後可以再直接跟我說想看哪一份文件。",
                "clear_pending": True,
            }
        choice = detect_display_choice(normalized) or detect_requested_action(normalized)
        if choice:
            return {
                "handled": True,
                "action": f"show_{choice}",
                "document": pending_document,
                "clear_pending": True,
            }

    if pending_candidates:
        if _is_cancel(normalized):
            return {
                "handled": True,
                "action": "cancel",
                "message": "已取消文件選擇。你之後可以再直接指定想看的文件。",
                "clear_pending": True,
            }
        selected = _select_candidate(normalized, pending_candidates)
        if selected:
            requested_action = detect_requested_action(normalized)
            if requested_action:
                return {
                    "handled": True,
                    "action": f"show_{requested_action}",
                    "document": selected,
                    "clear_pending": True,
                }
            return {
                "handled": True,
                "action": "prompt_choice",
                "message": _render_choice_prompt(selected),
                "document": selected,
                "set_pending_document": selected,
                "clear_pending_candidates": True,
            }

    if not documents and (_is_list_request(normalized) or _is_document_request(normalized) or pending_document or pending_candidates):
        return {
            "handled": True,
            "action": "no_docs",
            "message": "你目前還沒有可查閱的文件。可以先從右側文件中心上傳 PDF、DOCX、TXT 或 MD。",
            "clear_pending": True,
        }

    if _is_list_request(normalized):
        return {
            "handled": True,
            "action": "list",
            "message": _render_list_message(documents),
            "set_pending_candidates": documents[:8],
            "clear_pending": True,
        }

    if not _is_document_request(normalized) and not _looks_like_document_reference(normalized, documents):
        return None

    matches = _match_documents(normalized, documents)
    if not matches:
        return {
            "handled": True,
            "action": "no_match",
            "message": (
                "我還沒有找到明確對應的文件名稱。"
                "你可以說「列出文件」讓我先把目前可查閱的文件列給你。"
            ),
            "clear_pending": True,
        }

    requested_action = detect_requested_action(normalized)
    if len(matches) == 1:
        if requested_action:
            return {
                "handled": True,
                "action": f"show_{requested_action}",
                "document": matches[0],
                "clear_pending": True,
            }
        return {
            "handled": True,
            "action": "prompt_choice",
            "message": _render_choice_prompt(matches[0]),
            "document": matches[0],
            "set_pending_document": matches[0],
            "clear_pending_candidates": True,
        }

    return {
        "handled": True,
        "action": "prompt_candidates",
        "message": _render_candidate_prompt(matches),
        "set_pending_candidates": matches,
        "clear_pending_document": True,
    }

"""Deterministic document-intent handling for chat turns."""

from __future__ import annotations

import re
from typing import Any, Dict, List


PENDING_DOCUMENT_KEY = "pending_user_document_choice"
PENDING_CANDIDATES_KEY = "pending_user_document_candidates"

_LIST_PATTERNS = (
    "\u6709\u54ea\u4e9b\u6587\u4ef6",
    "\u6709\u54ea\u4e9b\u6a94\u6848",
    "\u76ee\u524d\u6709\u54ea\u4e9b\u6587\u4ef6",
    "\u76ee\u524d\u6709\u54ea\u4e9b\u6a94\u6848",
    "\u5217\u51fa\u6587\u4ef6",
    "\u5217\u51fa\u6a94\u6848",
    "\u6587\u4ef6\u5217\u8868",
    "\u6a94\u6848\u5217\u8868",
    "\u53ef\u67e5\u95b1\u7684\u6587\u4ef6",
    "\u53ef\u67e5\u95b1\u7684\u6a94\u6848",
)

_OPEN_PATTERNS = (
    "\u770b\u6587\u4ef6",
    "\u770b\u6a94\u6848",
    "\u6253\u958b\u6587\u4ef6",
    "\u6253\u958b\u6a94\u6848",
    "\u958b\u555f\u6587\u4ef6",
    "\u958b\u555f\u6a94\u6848",
    "\u986f\u793a\u6587\u4ef6",
    "\u986f\u793a\u6a94\u6848",
    "\u6211\u8981\u770b",
    "\u6211\u60f3\u770b",
    "\u5e6b\u6211\u770b",
    "show file",
    "open file",
    "show document",
    "open document",
)

_RECENT_PATTERNS = (
    "\u525b\u525b",
    "\u525b\u624d",
    "\u6700\u8fd1",
    "\u6700\u65b0",
    "\u4e0a\u6b21",
    "\u6700\u5f8c",
    "recent",
    "latest",
    "last",
)

_STOPWORDS = {
    "\u6211",
    "\u60f3",
    "\u8981",
    "\u770b",
    "\u4e00\u4e0b",
    "\u5e6b\u6211",
    "\u6253\u958b",
    "\u958b\u555f",
    "\u986f\u793a",
    "\u76f4\u63a5",
    "\u76ee\u524d",
    "\u6709\u54ea\u4e9b",
    "\u6587\u4ef6",
    "\u6a94\u6848",
    "file",
    "document",
    "show",
    "open",
    "read",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _extract_tokens(text: str) -> List[str]:
    parts = re.split(r"[^0-9a-zA-Z\u4e00-\u9fff._-]+", _normalize(text))
    return [part for part in parts if part and part not in _STOPWORDS]


def detect_display_choice(text: str) -> str | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if normalized in {"1", "\u9810\u89bd", "preview", "\u770b\u9810\u89bd", "\u670d\u52d9\u5167\u9810\u89bd", "\u5728\u670d\u52d9\u5167\u9810\u89bd"}:
        return "preview"
    if normalized in {"2", "\u6587\u5b57", "text", "\u6587\u5b57\u986f\u793a", "\u6587\u5b57\u8a0a\u606f", "\u986f\u793a\u6587\u5b57"}:
        return "text"
    if normalized in {"3", "\u9023\u7d50", "link", "\u958b\u555f\u9023\u7d50", "\u63d0\u4f9b\u9023\u7d50", "\u4e0b\u8f09\u9023\u7d50"}:
        return "link"
    return None


def _is_cancel(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {"\u53d6\u6d88", "\u7b97\u4e86", "\u4e0d\u7528\u4e86", "cancel", "never mind", "\u4e0d\u7528"}


def _extract_ordinal(text: str) -> int | None:
    normalized = _normalize(text)
    ordinal_mark = "\u7b2c"
    item_mark = "\u4efd"
    generic_mark = "\u500b"
    patterns = (
        rf"{ordinal_mark}\s*(\d+)\s*{item_mark}",
        rf"{ordinal_mark}\s*(\d+)\s*{generic_mark}",
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
        _contains_any(normalized, _OPEN_PATTERNS) or any(ext in normalized for ext in (".pdf", ".docx", ".txt", ".md"))
    ):
        return documents[:1]

    return []


def _render_list_message(documents: List[Dict[str, Any]]) -> str:
    lines = ["\u4f60\u76ee\u524d\u53ef\u67e5\u95b1\u7684\u6587\u4ef6\u5982\u4e0b\uff1a"]
    for idx, doc in enumerate(documents[:8], start=1):
        name = doc.get("display_name") or doc.get("original_filename") or doc.get("doc_id")
        lines.append(f"{idx}. {name}")
    lines.append("")
    lines.append("\u5982\u679c\u4f60\u60f3\u6253\u958b\u5176\u4e2d\u4e00\u4efd\uff0c\u76f4\u63a5\u8aaa\u300c\u6211\u8981\u770b\u7b2c 1 \u4efd\u300d\u6216\u300c\u6253\u958b \u6a94\u540d\u300d\u5373\u53ef\u3002")
    return "\n".join(lines)


def _render_choice_prompt(document: Dict[str, Any]) -> str:
    name = document.get("display_name") or document.get("original_filename") or document.get("doc_id")
    return (
        f"\u6211\u627e\u5230\u6587\u4ef6\u300c{name}\u300d\u3002\n"
        "\u4f60\u8981\u7528\u54ea\u7a2e\u65b9\u5f0f\u5c55\u793a\uff1f\n"
        "1. \u5728\u670d\u52d9\u5167\u9810\u89bd\n"
        "2. \u4ee5\u6587\u5b57\u8a0a\u606f\u986f\u793a\n"
        "3. \u63d0\u4f9b\u958b\u555f\u9023\u7d50\n"
        "\u4f60\u53ef\u4ee5\u76f4\u63a5\u56de\u8986\u300c\u9810\u89bd\u300d\u3001\u300c\u6587\u5b57\u300d\u6216\u300c\u9023\u7d50\u300d\u3002"
    )


def _render_candidate_prompt(documents: List[Dict[str, Any]]) -> str:
    lines = ["\u6211\u627e\u5230\u591a\u4efd\u53ef\u80fd\u7b26\u5408\u7684\u6587\u4ef6\uff0c\u8acb\u544a\u8a34\u6211\u8981\u6253\u958b\u54ea\u4e00\u4efd\uff1a"]
    for idx, doc in enumerate(documents[:5], start=1):
        name = doc.get("display_name") or doc.get("original_filename") or doc.get("doc_id")
        lines.append(f"{idx}. {name}")
    lines.append("")
    lines.append("\u4f60\u53ef\u4ee5\u56de\u8986\u300c\u7b2c 1 \u4efd\u300d\u6216\u76f4\u63a5\u8f38\u5165\u6a94\u540d\u3002")
    return "\n".join(lines)


def _is_document_request(text: str) -> bool:
    normalized = _normalize(text)
    if _contains_any(normalized, _OPEN_PATTERNS):
        return True
    if any(ext in normalized for ext in (".pdf", ".docx", ".txt", ".md")):
        return True
    return ("\u6587\u4ef6" in normalized or "\u6a94\u6848" in normalized) and any(
        word in normalized for word in ("\u770b", "\u6253\u958b", "\u958b\u555f", "\u986f\u793a", "\u53eb\u51fa", "\u627e")
    )


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
                "message": "\u5df2\u53d6\u6d88\u9019\u6b21\u6587\u4ef6\u5c55\u793a\u3002\u4f60\u4e4b\u5f8c\u53ef\u4ee5\u518d\u76f4\u63a5\u8ddf\u6211\u8aaa\u60f3\u770b\u54ea\u4e00\u4efd\u6587\u4ef6\u3002",
                "clear_pending": True,
            }
        choice = detect_display_choice(normalized)
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
                "message": "\u5df2\u53d6\u6d88\u6587\u4ef6\u9078\u64c7\u3002\u4f60\u4e4b\u5f8c\u53ef\u4ee5\u518d\u76f4\u63a5\u6307\u5b9a\u60f3\u770b\u7684\u6587\u4ef6\u3002",
                "clear_pending": True,
            }
        selected = _select_candidate(normalized, pending_candidates)
        if selected:
            return {
                "handled": True,
                "action": "prompt_choice",
                "message": _render_choice_prompt(selected),
                "document": selected,
                "set_pending_document": selected,
                "clear_pending_candidates": True,
            }

    if not documents and (
        _contains_any(normalized, _LIST_PATTERNS) or _is_document_request(normalized) or pending_document or pending_candidates
    ):
        return {
            "handled": True,
            "action": "no_docs",
            "message": "\u4f60\u76ee\u524d\u9084\u6c92\u6709\u53ef\u67e5\u95b1\u7684\u6587\u4ef6\u3002\u53ef\u4ee5\u5148\u5f9e\u53f3\u5074\u6587\u4ef6\u4e2d\u5fc3\u4e0a\u50b3 PDF\u3001DOCX\u3001TXT \u6216 MD\u3002",
            "clear_pending": True,
        }

    if _contains_any(normalized, _LIST_PATTERNS):
        return {
            "handled": True,
            "action": "list",
            "message": _render_list_message(documents),
            "clear_pending": True,
        }

    if not _is_document_request(normalized):
        return None

    matches = _match_documents(normalized, documents)
    if not matches:
        return {
            "handled": True,
            "action": "no_match",
            "message": (
                "\u6211\u9084\u6c92\u6709\u627e\u5230\u660e\u78ba\u5c0d\u61c9\u7684\u6587\u4ef6\u540d\u7a31\u3002"
                "\u4f60\u53ef\u4ee5\u8aaa\u300c\u5217\u51fa\u6587\u4ef6\u300d\u8b93\u6211\u5148\u628a\u76ee\u524d\u53ef\u67e5\u95b1\u7684\u6587\u4ef6\u5217\u7d66\u4f60\u3002"
            ),
            "clear_pending": True,
        }

    if len(matches) == 1:
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

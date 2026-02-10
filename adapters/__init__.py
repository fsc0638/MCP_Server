"""
Shared utilities for all model adapters.
Includes tag extraction and two-phase dynamic tool injection.
"""
import re
from typing import List, Dict, Any


def extract_tags(description: str) -> List[str]:
    """
    Extracts keyword tags from a skill description.
    Used by SkillRegistry at startup for fast tag-based matching.
    """
    # Remove common stop words and extract meaningful keywords
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "this", "that", "it", "for", "on", "in", "to", "of", "and",
        "or", "with", "as", "by", "at", "from", "will", "can", "do",
        "use", "using", "used", "tool", "script", "file", "data",
    }

    # Clean and tokenize
    words = re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', description.lower())
    tags = [w for w in words if w not in stop_words]

    # Deduplicate while preserving order
    seen = set()
    unique_tags = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)

    return unique_tags[:20]  # Cap at 20 tags per skill


def select_relevant_tools(
    user_query: str,
    all_tools: List[Dict[str, Any]],
    max_tools: int = 10
) -> List[Dict[str, Any]]:
    """
    Two-phase dynamic tool injection:
    Phase 1 — Tag Matching: fast keyword-based filter
    Phase 2 — Semantic Fallback: score remaining tools by word overlap
    """
    query_lower = user_query.lower()
    query_words = set(re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', query_lower))

    # Phase 1: Tag matching
    tag_matched = []
    unmatched = []
    for tool in all_tools:
        tags = tool.get("_tags", [])
        if any(tag in query_lower for tag in tags):
            tag_matched.append(tool)
        else:
            unmatched.append(tool)

    if len(tag_matched) >= max_tools:
        return tag_matched[:max_tools]

    # Phase 2: Semantic fallback (word overlap scoring)
    scored = []
    for tool in unmatched:
        desc = tool.get("_description_raw", "").lower()
        desc_words = set(re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', desc))
        overlap = len(query_words & desc_words)
        scored.append((overlap, tool))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = tag_matched + [t for _, t in scored]
    return result[:max_tools]

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
    max_tools: int = 25
) -> List[Dict[str, Any]]:
    """
    Two-phase dynamic tool injection:
    Phase 1 — Keyword Matching: match query words against tool name + description
    Phase 2 — Fallback: score remaining tools by word overlap

    Works with OpenAI tool format: {"type": "function", "function": {"name":..., "description":...}}
    """
    query_lower = user_query.lower()
    query_words = set(re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', query_lower))

    def get_tool_text(tool: Dict[str, Any]) -> str:
        """Extract searchable text from any tool format."""
        # OpenAI format
        if "function" in tool:
            fn = tool["function"]
            return f"{fn.get('name', '')} {fn.get('description', '')}".lower()
        # Gemini format
        if "name" in tool:
            return f"{tool.get('name', '')} {tool.get('description', '')}".lower()
        # Fallback: stringify
        return str(tool).lower()

    # Phase 1: Keyword matching on tool name + description
    matched = []
    unmatched = []
    for tool in all_tools:
        tool_text = get_tool_text(tool)
        if any(word in tool_text for word in query_words if len(word) > 2):
            matched.append(tool)
        else:
            unmatched.append(tool)

    if len(matched) >= max_tools:
        return matched[:max_tools]

    # Phase 2: Semantic fallback (word overlap scoring)
    scored = []
    for tool in unmatched:
        tool_text = get_tool_text(tool)
        tool_words = set(re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', tool_text))
        overlap = len(query_words & tool_words)
        scored.append((overlap, tool))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = matched + [t for _, t in scored]
    return result[:max_tools]

"""
Shared utilities for all model adapters.
Includes multilingual tag extraction (jieba + regex) and two-phase dynamic tool injection.

D-07 Optimization:
  - Loads stop words from shared/stop_words.json (一處管理)
  - Uses jieba for Chinese tokenization
  - Japanese: regex-based continuous kanji/kana group extraction
  - Synonym expansion for cross-language tag unification
  - Weighted tags: name tags get 2x weight
"""
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Set

logger = logging.getLogger("MCP_Server.Adapters")

# ─── Module-level: Load stop words and synonyms once ─────────────────────────

_STOP_WORDS: Set[str] = set()
_SYNONYMS: Dict[str, List[str]] = {}
_SYNONYM_MAP: Dict[str, str] = {}  # reverse lookup: variant → canonical

def _load_stop_words():
    """Load multilingual stop words from shared/stop_words.json."""
    global _STOP_WORDS, _SYNONYMS, _SYNONYM_MAP

    # Try multiple paths (submodule or direct)
    candidates = [
        Path(__file__).resolve().parent.parent / "Agent_skills" / "shared" / "stop_words.json",
        Path(__file__).resolve().parent.parent / "shared" / "stop_words.json",
    ]

    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Merge all language stop words
                for lang in ("zh", "en", "ja"):
                    _STOP_WORDS.update(w.lower() for w in data.get(lang, []))

                # Build synonym map
                _SYNONYMS = data.get("synonyms", {})
                for canonical, variants in _SYNONYMS.items():
                    canon_lower = canonical.lower()
                    _SYNONYM_MAP[canon_lower] = canon_lower
                    for v in variants:
                        _SYNONYM_MAP[v.lower()] = canon_lower

                logger.info(f"Loaded {len(_STOP_WORDS)} stop words, {len(_SYNONYMS)} synonym groups from {path.name}")
                return
            except Exception as e:
                logger.warning(f"Failed to load stop_words.json: {e}")

    logger.warning("stop_words.json not found, using empty stop words")

# Load on import
_load_stop_words()

# ─── Jieba initialization (lazy) ─────────────────────────────────────────────

_jieba = None

def _get_jieba():
    """Lazy-load jieba to avoid startup delay if not needed."""
    global _jieba
    if _jieba is None:
        try:
            import jieba
            jieba.setLogLevel(logging.WARNING)  # Suppress jieba's noisy logging
            _jieba = jieba
        except ImportError:
            logger.warning("jieba not installed — falling back to regex tokenization for Chinese")
    return _jieba


# ─── Tokenization ────────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """
    Multilingual tokenizer:
      - Chinese: jieba precise mode (falls back to regex if jieba unavailable)
      - Japanese: regex-based continuous kanji groups + kana groups
      - English: standard word boundary split
    """
    tokens = []
    text_lower = text.lower()

    # Phase 1: English words (2+ chars)
    en_words = re.findall(r'[a-zA-Z]{2,}', text_lower)
    tokens.extend(en_words)

    # Phase 2: Chinese text — jieba segmentation
    # Extract CJK unified ideograph blocks (Chinese/Japanese kanji)
    cjk_blocks = re.findall(r'[\u4e00-\u9fff]+', text)
    jieba_mod = _get_jieba()
    if jieba_mod and cjk_blocks:
        for block in cjk_blocks:
            # Jieba handles both Chinese and kanji compounds like 契約書
            words = jieba_mod.cut(block, cut_all=False)
            tokens.extend(w for w in words if len(w) >= 2)
    elif cjk_blocks:
        # Fallback: treat continuous CJK as single tokens (preserves 契約書 etc.)
        tokens.extend(block for block in cjk_blocks if len(block) >= 2)

    # Phase 3: Japanese kana groups (hiragana/katakana runs, 2+ chars)
    hiragana_groups = re.findall(r'[\u3040-\u309f]{2,}', text)
    katakana_groups = re.findall(r'[\u30a0-\u30ff]{2,}', text)
    tokens.extend(hiragana_groups)
    tokens.extend(katakana_groups)

    return tokens


def _normalize_synonym(word: str) -> str:
    """Map a word to its canonical synonym form if exists."""
    return _SYNONYM_MAP.get(word.lower(), word.lower())


# ─── Public API ──────────────────────────────────────────────────────────────

def extract_tags(description: str, name: str = "") -> List[str]:
    """
    Extracts keyword tags from a skill name + description.
    - Filters multilingual stop words
    - Applies synonym normalization
    - Name tokens get 2x weight (appear first and are duplicated for scoring)

    Returns deduplicated tag list, capped at 30.
    """
    # Tokenize name (higher weight) and description
    name_tokens = _tokenize(name) if name else []
    desc_tokens = _tokenize(description)

    # Filter stop words and normalize synonyms
    def process(tokens):
        return [_normalize_synonym(w) for w in tokens if w.lower() not in _STOP_WORDS]

    name_tags = process(name_tokens)
    desc_tags = process(desc_tokens)

    # Name tags get 2x weight (prepend twice)
    weighted = name_tags + name_tags + desc_tags

    # Deduplicate while preserving order (weighted items appear earlier)
    seen = set()
    unique_tags = []
    for tag in weighted:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)

    return unique_tags[:30]


def select_relevant_tools(
    user_query: str,
    all_tools: List[Dict[str, Any]],
    max_tools: int = 25
) -> List[Dict[str, Any]]:
    """
    Two-phase dynamic tool injection with multilingual tokenization:
    Phase 1 — Keyword Matching: match tokenized query against tool name + description
    Phase 2 — Fallback: score remaining tools by token overlap

    Filters stop words and applies synonym normalization on both sides.
    """
    # Tokenize and normalize user query
    query_tokens = _tokenize(user_query)
    query_words = set(
        _normalize_synonym(w) for w in query_tokens if w.lower() not in _STOP_WORDS
    )

    def get_tool_text(tool: Dict[str, Any]) -> str:
        """Extract searchable text from any tool format."""
        if "function" in tool:
            fn = tool["function"]
            return f"{fn.get('name', '')} {fn.get('description', '')}"
        if "name" in tool:
            return f"{tool.get('name', '')} {tool.get('description', '')}"
        return str(tool)

    def get_tool_tokens(tool: Dict[str, Any]) -> set:
        """Tokenize + normalize + filter stop words for a tool."""
        text = get_tool_text(tool)
        tokens = _tokenize(text)
        return set(_normalize_synonym(w) for w in tokens if w.lower() not in _STOP_WORDS)

    # Phase 1: Keyword matching
    matched = []
    unmatched = []
    for tool in all_tools:
        tool_tokens = get_tool_tokens(tool)
        if query_words & tool_tokens:  # Any overlap
            matched.append(tool)
        else:
            unmatched.append(tool)

    if len(matched) >= max_tools:
        return matched[:max_tools]

    # Phase 2: Semantic fallback (token overlap scoring)
    scored = []
    for tool in unmatched:
        tool_tokens = get_tool_tokens(tool)
        overlap = len(query_words & tool_tokens)
        scored.append((overlap, tool))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = matched + [t for _, t in scored]
    return result[:max_tools]

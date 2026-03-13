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


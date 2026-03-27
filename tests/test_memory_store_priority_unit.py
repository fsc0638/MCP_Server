import json
from pathlib import Path

from server.services.memory_store import MemoryStore


def test_priority_profiles_over_sessions(tmp_path: Path):
    ms = MemoryStore(tmp_path)

    # sessions first
    ms.upsert_long_term_behavior_rules(
        {
            "style": [
                {"text": "風格: 繁體中文", "source_type": "sessions", "source": "s1", "evidence": "a"}
            ],
            "taboos": [],
            "group_rules": [],
        },
        history_limit=1000,
    )

    # profiles overwrites
    ms.upsert_long_term_behavior_rules(
        {
            "style": [
                {"text": "風格: 繁體中文", "source_type": "profiles", "source": "p1", "evidence": "b"}
            ],
            "taboos": [],
            "group_rules": [],
        },
        history_limit=1000,
    )

    data = json.loads((tmp_path / "memory" / "memory_store.json").read_text(encoding="utf-8"))
    style = data["long_term"]["behavior_rules"]["style"][0]
    assert style["source_type"] == "profiles"


def test_priority_reject_lower_weight(tmp_path: Path):
    ms = MemoryStore(tmp_path)

    ms.upsert_long_term_behavior_rules(
        {
            "taboos": [
                {"text": "禁忌: 不要談政治", "source_type": "profiles", "source": "p1", "evidence": "x"}
            ],
            "style": [],
            "group_rules": [],
        },
        history_limit=1000,
    )

    # uploads tries to override
    ms.upsert_long_term_behavior_rules(
        {
            "taboos": [
                {"text": "禁忌: 不要談政治", "source_type": "uploads", "source": "u1", "evidence": "y"}
            ],
            "style": [],
            "group_rules": [],
        },
        history_limit=1000,
    )

    data = json.loads((tmp_path / "memory" / "memory_store.json").read_text(encoding="utf-8"))
    taboo = data["long_term"]["behavior_rules"]["taboos"][0]
    assert taboo["source_type"] == "profiles"

    assert any(ev.get("type") == "reject" for ev in data.get("change_log", []))

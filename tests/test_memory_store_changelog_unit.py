import json
from pathlib import Path

from server.services.memory_store import MemoryStore


def test_memory_store_changelog_overwrite_and_limit(tmp_path: Path):
    ms = MemoryStore(tmp_path)

    br1 = {
        "style": [{"text": "風格: 繁體中文", "source_type": "profiles", "source": "p1", "evidence": "x"}],
        "taboos": [],
        "group_rules": [],
    }
    ms.upsert_long_term_behavior_rules(br1, history_limit=5)

    br2 = {
        "style": [{"text": "風格: 繁體中文", "source_type": "profiles", "source": "p2", "evidence": "y"}],
        "taboos": [],
        "group_rules": [],
    }
    ms.upsert_long_term_behavior_rules(br2, history_limit=5)

    data = json.loads((tmp_path / "memory" / "memory_store.json").read_text(encoding="utf-8"))
    assert "change_log" in data
    assert len(data["change_log"]) >= 1
    assert data["change_log"][-1]["type"] in ("add", "overwrite")

    # push over limit
    for i in range(10):
        ms.upsert_long_term_behavior_rules(
            {"style": [{"text": f"風格: X{i}"}], "taboos": [], "group_rules": []},
            history_limit=5,
        )
    data2 = json.loads((tmp_path / "memory" / "memory_store.json").read_text(encoding="utf-8"))
    assert len(data2["change_log"]) <= 5

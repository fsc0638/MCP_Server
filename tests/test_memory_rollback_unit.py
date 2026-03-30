import json
from pathlib import Path

from server.services.memory_store import MemoryStore
from server.services.memory_rollback import rollback_rule


def test_rollback_overwrite(tmp_path: Path):
    ms = MemoryStore(tmp_path)

    br1 = {
        "style": [{"text": "風格: 繁體中文", "source_type": "sessions", "source": "s1"}],
        "taboos": [],
        "group_rules": [],
    }
    ms.upsert_long_term_behavior_rules(br1, history_limit=1000)

    br2 = {
        "style": [{"text": "風格: 繁體中文", "source_type": "profiles", "source": "p1"}],
        "taboos": [],
        "group_rules": [],
    }
    ms.upsert_long_term_behavior_rules(br2, history_limit=1000)

    # rollback to old (sessions)
    out = rollback_rule(tmp_path, kind="style", text="風格: 繁體中文", to="old")
    style = out["long_term"]["behavior_rules"]["style"][0]
    assert style["source_type"] == "sessions"
    assert any(ev.get("type") == "rollback" for ev in out.get("change_log", []))

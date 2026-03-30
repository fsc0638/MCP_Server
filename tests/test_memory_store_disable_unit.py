import json
from pathlib import Path

from server.services.memory_store import MemoryStore


def test_disable_missing_rules(tmp_path: Path):
    ms = MemoryStore(tmp_path)

    # initial set has two rules
    ms.upsert_long_term_behavior_rules(
        {
            "style": [{"text": "風格: 繁體中文", "source_type": "profiles", "source": "p"}],
            "taboos": [{"text": "禁忌: 不要談政治", "source_type": "profiles", "source": "p"}],
            "group_rules": [],
        },
        disable_missing=False,
    )

    # next update removes taboo; should disable it
    ms.upsert_long_term_behavior_rules(
        {
            "style": [{"text": "風格: 繁體中文", "source_type": "profiles", "source": "p"}],
            "taboos": [],
            "group_rules": [],
        },
        disable_missing=True,
        history_limit=1000,
    )

    data = json.loads((tmp_path / "memory" / "memory_store.json").read_text(encoding="utf-8"))
    assert data["long_term"]["behavior_rules"]["taboos"] == []
    assert any(d.get("text") == "禁忌: 不要談政治" for d in data.get("disabled", {}).get("taboos", []))
    assert any(ev.get("type") == "disable" for ev in data.get("change_log", []))

import json
from pathlib import Path

from server.services.memory_store import MemoryStore
from server.services.memory_store_updater import update_memory_store


def test_memory_store_updater_creates_and_updates(tmp_path: Path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)

    # Seed behavior_rules.json
    br = {
        "style": [{"text": "風格: 繁體中文", "source_type": "profiles", "source": "workspace/profiles/u1.json", "evidence": "風格: 繁體中文"}],
        "taboos": [{"text": "禁忌: 不要談政治", "source_type": "profiles", "source": "workspace/profiles/u1.json", "evidence": "禁忌: 不要談政治"}],
        "group_rules": [],
    }
    (mem / "behavior_rules.json").write_text(json.dumps(br, ensure_ascii=False), encoding="utf-8")

    # Seed continuous_learner_state.json
    st = {"last_run_at": "t", "last_recent_count": 2, "last_file_count": 3, "last_mode": "no_llm"}
    (mem / "continuous_learner_state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

    out = update_memory_store(tmp_path)

    store_path = mem / "memory_store.json"
    assert store_path.exists()

    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["long_term"]["behavior_rules"]["style"]
    assert data["short_term"]["recent_ticks"]

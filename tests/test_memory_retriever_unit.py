import json
from pathlib import Path

from server.services.memory_retriever import MemoryRetriever, render_memory_injection


def test_memory_retriever_returns_matching_rules(tmp_path: Path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)

    store = {
        "version": 1,
        "updated_at": "x",
        "long_term": {
            "behavior_rules": {
                "style": [{"text": "風格: 繁體中文"}],
                "taboos": [{"text": "禁忌: 不要談政治"}],
                "group_rules": [{"text": "群組規則: 群組不回覆"}],
            },
            "notes": [],
        },
        "short_term": {"recent_ticks": [], "recent_notes": []},
    }
    (mem / "memory_store.json").write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")

    r = MemoryRetriever(tmp_path)
    items = r.retrieve("政治", max_items=5)
    assert any("不要談政治" in it["text"] for it in items)

    inj = render_memory_injection(items)
    assert "相關記憶" in inj

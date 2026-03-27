import json
from pathlib import Path

from server.services.behavior_rule_extractor import BehaviorRuleExtractor


def test_behavior_rule_extractor_writes_files_with_sources(tmp_path: Path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)

    snap = {
        "files": [
            {
                "source": "profiles",
                "path": "workspace/profiles/u1.json",
                "preview": "禁忌: 不要談政治\n風格: 繁體中文\n群組規則: 群組不回覆",
            }
        ],
        "messages": [
            {"source": "sessions", "session_id": "line_u1", "role": "user", "content": "群組訊息不要回", "created_at": 1}
        ],
    }
    (mem / "learning_snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    ex = BehaviorRuleExtractor(tmp_path)
    out = ex.write()

    assert (mem / "behavior_rules.json").exists()
    assert (mem / "behavior_rules.md").exists()

    # Ensure structure contains source info
    assert any(r.get("source_type") == "profiles" and r.get("source") == "workspace/profiles/u1.json" for r in out["taboos"])
    assert any(r.get("source_type") == "profiles" for r in out["style"])
    assert any(r.get("source_type") in ("profiles", "sessions") for r in out["group_rules"])

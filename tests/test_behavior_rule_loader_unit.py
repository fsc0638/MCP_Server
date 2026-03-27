import json
from pathlib import Path

from server.services.behavior_rule_loader import render_behavior_rules_appendix


def test_render_behavior_rules_appendix(tmp_path: Path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)

    data = {
        "style": [{"text": "風格: 繁體中文"}],
        "taboos": [{"text": "禁忌: 不要談政治"}],
        "group_rules": [{"text": "群組規則: 群組不回覆"}],
    }
    (mem / "behavior_rules.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    out = render_behavior_rules_appendix(tmp_path, max_each=3)
    assert "行為規則" in out
    assert "風格" in out and "繁體中文" in out
    assert "禁忌" in out and "不要談政治" in out
    assert "群組規則" in out and "群組不回覆" in out

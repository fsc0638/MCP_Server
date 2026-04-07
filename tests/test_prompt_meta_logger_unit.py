import json
from pathlib import Path

from server.services.prompt_meta_logger import append_prompt_meta


def test_append_prompt_meta_writes_jsonl(tmp_path: Path):
    append_prompt_meta(tmp_path, "s1", {"a": 1})
    p = tmp_path / "workspace" / "sessions" / "s1_prompt_meta.jsonl"
    assert p.exists()
    line = p.read_text(encoding="utf-8").strip().splitlines()[-1]
    obj = json.loads(line)
    assert obj["session_id"] == "s1"
    assert obj["meta"]["a"] == 1

import json
from pathlib import Path

from server.services.learning_compactor import LearningCompactor


def test_learning_compactor_writes_snapshot(tmp_path: Path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    buf = mem_dir / "continuous_learning_buffer.jsonl"
    buf.write_text(
        "\n".join(
            [
                json.dumps({"type": "file", "path": "workspace/profiles/u1.json", "mtime": 1, "bytes": 1, "preview": "p"}),
                json.dumps({"type": "file", "path": "Agent_workspace/line_uploads/line_u1/a.txt", "mtime": 2, "bytes": 1, "preview": "u"}),
                json.dumps({"type": "message", "session_id": "s1", "role": "user", "content": "hi", "created_at": 3}),
            ]
        ),
        encoding="utf-8",
    )

    comp = LearningCompactor(tmp_path)
    snap = comp.write_snapshot()

    assert (mem_dir / "learning_snapshot.json").exists()
    assert (mem_dir / "learning_snapshot.md").exists()
    assert snap["counts"]["messages"] == 1
    assert snap["counts"]["files"] == 2

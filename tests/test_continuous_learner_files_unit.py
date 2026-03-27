import json
from pathlib import Path

from server.services.continuous_learner import ContinuousLearner


def test_continuous_learner_scans_profiles_and_uploads_and_skips_images(tmp_path: Path):
    # Create fake profile + upload files
    (tmp_path / "workspace" / "profiles").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Agent_workspace" / "line_uploads" / "line_u1").mkdir(parents=True, exist_ok=True)
    sessions_dir = tmp_path / "workspace" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    prof = tmp_path / "workspace" / "profiles" / "u1.json"
    prof.write_text("{\"name\":\"u1\",\"habit\":\"coffee\"}", encoding="utf-8")

    up_txt = tmp_path / "Agent_workspace" / "line_uploads" / "line_u1" / "note.txt"
    up_txt.write_text("hello uploads", encoding="utf-8")

    # Image should be skipped
    up_img = tmp_path / "Agent_workspace" / "line_uploads" / "line_u1" / "x.png"
    up_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    # msg_cache should be included
    cache = sessions_dir / "line_u1_msg_cache.json"
    cache.write_text(json.dumps({"m1": {"created_at": "2026-01-01T00:00:00", "text": "x"}}), encoding="utf-8")

    learner = ContinuousLearner(tmp_path)
    learner.save_state({"last_seen_ts": 0, "last_seen_mtime": 0.0, "runs": 0})
    learner.tick(llm_callable=None)

    buf_path = tmp_path / "memory" / "continuous_learning_buffer.jsonl"
    assert buf_path.exists()
    text = buf_path.read_text(encoding="utf-8")

    assert "workspace/profiles/u1.json" in text
    assert "Agent_workspace/line_uploads/line_u1/note.txt" in text
    assert "line_u1_msg_cache.json" in text
    assert "x.png" not in text

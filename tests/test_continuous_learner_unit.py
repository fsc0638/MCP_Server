from pathlib import Path
import json

from server.services.continuous_learner import ContinuousLearner


def test_continuous_learner_tick_writes_state_and_buffers(tmp_path: Path):
    # Arrange: create a fake session file with created_at timestamps.
    sessions_dir = tmp_path / "workspace" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "s1.json").write_text(
        json.dumps(
            [
                {"role": "user", "content": "hi", "created_at": 1000},
                {"role": "assistant", "content": "hello", "created_at": 1001},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    learner = ContinuousLearner(tmp_path)

    # Seed state so we pick up the fake messages.
    learner.save_state({"last_seen_ts": 999, "runs": 0})

    # Act
    learner.tick(llm_callable=None)

    # Assert: state
    state_path = tmp_path / "memory" / "continuous_learner_state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["runs"] == 1
    assert state["last_recent_count"] == 2

    # Assert: JSONL buffer
    buf_path = tmp_path / "memory" / "continuous_learning_buffer.jsonl"
    assert buf_path.exists()
    buf_text = buf_path.read_text(encoding="utf-8")
    assert "\"type\": \"message\"" in buf_text
    assert "\"session_id\": \"s1\"" in buf_text

    # Assert: MEMORY.md appended
    mem_path = tmp_path / "memory" / "MEMORY.md"
    assert mem_path.exists()
    mem_text = mem_path.read_text(encoding="utf-8")
    assert "Continuous Learner Tick" in mem_text

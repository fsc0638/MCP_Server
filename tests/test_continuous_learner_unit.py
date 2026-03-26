from pathlib import Path

from server.services.continuous_learner import ContinuousLearner


def test_continuous_learner_tick_writes_state(tmp_path: Path):
    learner = ContinuousLearner(tmp_path)
    learner.tick(llm_callable=None)

    state_path = tmp_path / "memory" / "continuous_learner_state.json"
    assert state_path.exists()

    state = state_path.read_text(encoding="utf-8")
    assert "last_run_at" in state
    assert "runs" in state
    assert "no_llm" in state

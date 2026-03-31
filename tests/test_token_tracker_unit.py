import json
from pathlib import Path

from server.services.token_tracker import TokenTracker


def test_token_summary_separates_chat_calls(tmp_path: Path):
    tr = TokenTracker(str(tmp_path))

    # Write a few usage records
    tr.record_usage(session_id="s1", user_id="u1", chat_type="personal", chat_id="", skill="(chat)", total_tokens=10)
    tr.record_usage(session_id="s1", user_id="u1", chat_type="personal", chat_id="", skill="get_weather", total_tokens=20)
    tr.record_usage(session_id="s2", user_id="u2", chat_type="group", chat_id="g1", skill="(chat)", total_tokens=5)

    tr.rebuild_summary()

    summary = json.loads((tmp_path / "workspace" / "analytics" / "token_summary.json").read_text(encoding="utf-8"))

    assert summary["total"]["chat_calls"] == 2
    assert summary["total"]["skill_calls"] == 1

    assert summary["by_user"]["u1"]["chat_calls"] == 1
    assert summary["by_user"]["u1"]["skill_calls"] == 1

    gk = "line_group_g1"
    assert summary["by_group"][gk]["chat_calls"] == 1
    assert summary["by_group"][gk]["skill_calls"] == 0

    # daily has both fields
    day = summary["last_updated"][:10]
    assert day in summary["daily"]
    assert "chat_calls" in summary["daily"][day]

import json
from pathlib import Path

from server.services.session_summarizer import SessionSummarizer, render_session_summary_injection


def test_session_summarizer_writes_cache_and_reuses(tmp_path: Path):
    sessions = tmp_path / "workspace" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)

    sid = "s1"
    msgs = [
        {"role": "user", "content": "hello", "created_at": 1},
        {"role": "assistant", "content": "hi", "created_at": 2},
    ]
    (sessions / f"{sid}.json").write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")

    summ = SessionSummarizer(tmp_path)
    s1 = summ.maybe_update(sid, min_new_messages=1)
    assert (sessions / f"{sid}_summary.json").exists()
    inj = render_session_summary_injection(s1)
    assert "對話摘要" in inj

    # Append a small number of new messages; should reuse if threshold not reached
    msgs2 = msgs + [{"role": "user", "content": "q", "created_at": 3}]
    (sessions / f"{sid}.json").write_text(json.dumps(msgs2, ensure_ascii=False), encoding="utf-8")
    s2 = summ.maybe_update(sid, min_new_messages=6)
    assert s2["message_count"] == s1["message_count"]

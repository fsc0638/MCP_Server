import json
from pathlib import Path

from server.services.memory_store import MemoryStore


def test_short_term_tick_ttl_prune(tmp_path: Path):
    ms = MemoryStore(tmp_path)

    # Seed with an old tick
    data = ms.load()
    data["short_term"]["recent_ticks"] = [
        {"ts": "2000-01-01T00:00:00", "last_run_at": "old"},
        {"ts": "2099-01-01T00:00:00", "last_run_at": "new"},
    ]
    ms.save(data)

    ms.append_short_term_tick({"last_run_at": "now"}, limit=50, max_age_days=7)

    out = json.loads((tmp_path / "memory" / "memory_store.json").read_text(encoding="utf-8"))
    ticks = out["short_term"]["recent_ticks"]
    assert all(t.get("ts") != "2000-01-01T00:00:00" for t in ticks)
    assert any(t.get("last_run_at") == "new" for t in ticks)

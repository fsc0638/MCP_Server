def test_audit_logger_writes_row(tmp_path, monkeypatch):
    from server.services import db as dbmod

    # Force db path into temp directory
    def _tmp_db_path():
        p = tmp_path / "agentk.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(dbmod, "get_db_path", _tmp_db_path)

    from server.services.audit_logger import log_event

    event_id = log_event(
        correlation_id="corr-1",
        subject_id="line_test",
        action="skill.execute",
        resource_type="skill",
        resource_id="mcp-python-executor",
        decision="allow",
        input_obj={"x": 1},
        output_obj={"y": 2},
    )

    # Verify row exists
    conn = dbmod.connect(_tmp_db_path())
    dbmod.init_db(conn)
    row = conn.execute("SELECT * FROM audit_events WHERE event_id=?", (event_id,)).fetchone()
    assert row is not None
    assert row["correlation_id"] == "corr-1"
    assert row["subject_id"] == "line_test"
    assert row["action"] == "skill.execute"
    conn.close()

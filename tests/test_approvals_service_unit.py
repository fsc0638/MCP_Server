def test_approval_create_and_resolve(tmp_path, monkeypatch):
    from server.services import db as dbmod

    def _tmp_db_path():
        p = tmp_path / "agentk.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(dbmod, "get_db_path", _tmp_db_path)

    from server.services.approvals_service import create_approval, get_approval, resolve_approval

    aid = create_approval(
        correlation_id="corr-1",
        requested_by_subject_id="line_x",
        action="line.user_push",
        resource_type="line",
        resource_id="Uxxx",
        request_summary="push test",
        payload={"to": "Uxxx", "text": "hi"},
        ttl_seconds=600,
    )

    ap = get_approval(aid)
    assert ap is not None
    assert ap["status"] == "pending"

    ok = resolve_approval(approval_id=aid, status="approved", resolved_by_subject_id="line_x")
    assert ok is True

    ap2 = get_approval(aid)
    assert ap2["status"] == "approved"

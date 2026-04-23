import json


def test_resume_endpoint_replays_with_checkpoint(monkeypatch, tmp_path):
    # Force db path to temp
    from server.services import db as dbmod

    def _tmp_db_path():
        p = tmp_path / "agentk.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(dbmod, "get_db_path", _tmp_db_path)

    # Create a workflow file under templates (will be found by resume search)
    from pathlib import Path
    wf_dir = tmp_path / "workspace" / "workflows" / "personal" / "default"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "wf_test.json").write_text(json.dumps({
        "workflow_id": "wf_test",
        "display_name": "WF",
        "blocks": [
            {"id": "s", "type": "start"},
            {"id": "b1", "type": "mcp-python-executor", "config": {"params": {"code": "print(1)"}}},
            {"id": "e", "type": "end"},
        ],
        "connections": [{"from": "s", "to": "b1"}, {"from": "b1", "to": "e"}],
        "variables": [],
        "execution": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # Force PROJECT_ROOT for workflow discovery
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))

    # Fake UMA and executor behavior: first run requires approval at b1, then success.
    class _FakeRegistry:
        def get_skill(self, name):
            return {"metadata": {"_env_ready": True, "risk_level": "high"}}

    class _FakeUMA:
        def __init__(self):
            self.registry = _FakeRegistry()
            self.calls = 0

        def execute_tool_call(self, skill_name, arguments):
            self.calls += 1
            # Always succeed in this test
            return {"output": "ok"}

    _uma = _FakeUMA()
    monkeypatch.setattr("server.dependencies.uma.get_uma_instance", lambda: _uma)

    # Create approval with approved status and payload
    from server.services.approvals_service import create_approval, resolve_approval

    aid = create_approval(
        correlation_id="run_test",
        requested_by_subject_id="line_u",
        action="mcp-high-risk-demo",
        resource_type="workflow_block",
        resource_id="wf:1",
        request_summary="x",
        payload={"workflow_id": "wf_test", "run_id": "run_test", "user_input": "hi", "user_inputs": {}},
        ttl_seconds=600,
    )
    assert resolve_approval(approval_id=aid, status="approved", resolved_by_subject_id="line_u")

    # Call endpoint without cookie should fail
    from fastapi.testclient import TestClient
    from server.app import app
    client = TestClient(app)
    r = client.post(f"/api/workflows/resume/{aid}")
    assert r.status_code in (401, 403)

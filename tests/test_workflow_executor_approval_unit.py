import json


def test_workflow_executor_requires_approval(monkeypatch, tmp_path):
    # Patch approvals DB path to temp
    from server.services import db as dbmod

    def _tmp_db_path():
        p = tmp_path / "agentk.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(dbmod, "get_db_path", _tmp_db_path)

    # Patch UMA to return requires_approval for a specific skill
    class _FakeRegistry:
        def get_skill(self, name):
            return {"metadata": {"_env_ready": True, "risk_level": "high"}}

    class _FakeUMA:
        def __init__(self):
            self.registry = _FakeRegistry()
        def execute_tool_call(self, skill_name, arguments):
            return {"status": "requires_approval", "pending_args": json.loads(arguments)}

    def _fake_get_uma():
        return _FakeUMA()

    monkeypatch.setattr("server.dependencies.uma.get_uma_instance", _fake_get_uma)

    from server.services.workflow_executor import WorkflowExecutor

    wf = {
        "workflow_id": "wf_test",
        "display_name": "WF Test",
        "blocks": [
            {"id": "s", "type": "start"},
            {"id": "b1", "type": "mcp-high-risk-demo", "config": {"params": {"x": "1"}}},
            {"id": "e", "type": "end"},
        ],
        "connections": [
            {"from": "s", "to": "b1"},
            {"from": "b1", "to": "e"},
        ],
        "variables": [],
        "execution": {},
    }

    import asyncio
    ex = WorkflowExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        ex.execute(wf, user_input="hi", user_context={"user_id": "line_u"})
    )

    assert result["status"] == "requires_approval"
    # ensure an approval_id is produced in results
    assert any(r.get("approval_id") for r in result.get("results", []) if r.get("status") == "requires_approval")

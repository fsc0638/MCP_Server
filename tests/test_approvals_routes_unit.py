def test_list_approvals_requires_signin():
    from fastapi.testclient import TestClient
    from server.app import app

    client = TestClient(app)
    resp = client.get('/api/approvals?status=pending')
    assert resp.status_code in (401, 403)

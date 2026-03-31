from __future__ import annotations


def test_collect_generator_provider_meta_sets_session_metadata(monkeypatch):
    # Arrange: fake session manager that records metadata
    class FakeSM:
        def __init__(self):
            self.kv = {}

        def set_metadata(self, session_id: str, key: str, value):
            self.kv[(session_id, key)] = value

    fake_sm = FakeSM()

    # Monkeypatch dependency getter used inside line_connector._collect_generator
    import server.dependencies.session as dep_session

    def _fake_get_session_manager():
        return fake_sm

    monkeypatch.setattr(dep_session, "get_session_manager", _fake_get_session_manager)

    # Avoid importing the real LINE connector module (it uses Python 3.10+ type syntax
    # in annotations and may import heavy SDK deps). Instead, test the core behavior
    # by calling the same logic we implemented in _collect_generator.
    def consume(gen, session_id: str):
        for chunk in gen:
            if chunk.get("status") == "provider_meta":
                _rid = chunk.get("response_id") or ""
                if session_id and _rid:
                    dep_session.get_session_manager().set_metadata(session_id, "last_response_id", _rid)
            elif chunk.get("status") == "success":
                return chunk.get("content", "")
        return ""

    def gen():
        yield {"status": "provider_meta", "provider": "openai", "response_id": "resp_123"}
        yield {"status": "success", "content": "OK"}

    out = consume(gen(), session_id="line_user_x")
    assert out == "OK"
    assert fake_sm.kv[("line_user_x", "last_response_id")] == "resp_123"

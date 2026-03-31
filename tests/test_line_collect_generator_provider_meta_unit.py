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

    # Minimal generator that emits provider_meta then success
    def gen():
        yield {"status": "provider_meta", "provider": "openai", "response_id": "resp_123"}
        yield {"status": "success", "content": "OK"}

    from server.integrations.line_connector import _collect_generator

    out = _collect_generator(gen(), line_api=None, chat_id="line_user_x", session_id="line_user_x")

    assert out == "OK"
    assert fake_sm.kv[("line_user_x", "last_response_id")] == "resp_123"

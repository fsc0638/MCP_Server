def test_policy_high_risk_requires_approval():
    from server.services.policy import authorize

    d = authorize(
        subject_ctx={"role": "worker", "user_id": "line_x"},
        action="line.user_push",
        resource_type="line",
        resource_id="Uxxx",
        context={},
    )
    assert d.allow is True
    assert d.requires_approval is True


def test_policy_unknown_role_defaults_guest():
    from server.services.policy import authorize

    d = authorize(
        subject_ctx={"role": "", "user_id": "line_x"},
        action="skill.execute",
        resource_type="skill",
        resource_id="mcp-python-executor",
        context={},
    )
    assert d.allow is True

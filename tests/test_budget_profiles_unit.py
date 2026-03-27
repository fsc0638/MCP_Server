from server.services.budget_profiles import get_budget_for_model


def test_budget_profiles_defaults():
    bp = get_budget_for_model(None)
    assert bp.max_input_tokens >= 8000


def test_budget_profiles_gpt4o():
    bp = get_budget_for_model("gpt-4o")
    assert bp.max_input_tokens >= 16000


def test_budget_profiles_gpt4o_mini():
    bp = get_budget_for_model("gpt-4o-mini")
    assert bp.max_input_tokens >= 8000

from server.services.learning_policy import LearningPolicy


def test_learning_policy_priority_A_profiles_over_uploads():
    p = LearningPolicy()
    a = {"source": "profiles", "x": 1}
    b = {"source": "uploads", "x": 2}
    assert p.choose(a, b)["source"] == "profiles"


def test_learning_policy_priority_A_sessions_over_uploads():
    p = LearningPolicy()
    a = {"source": "sessions", "x": 1}
    b = {"source": "uploads", "x": 2}
    assert p.choose(a, b)["source"] == "sessions"

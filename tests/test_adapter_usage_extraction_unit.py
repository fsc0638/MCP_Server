from types import SimpleNamespace


def test_claude_usage_extraction_shape():
    # Mirrors anthropic.types.usage.Usage fields we depend on.
    usage = SimpleNamespace(input_tokens=123, output_tokens=45)
    response = SimpleNamespace(id="msg_1", usage=usage)

    inp = int(getattr(getattr(response, "usage", None), "input_tokens", 0) or 0)
    out = int(getattr(getattr(response, "usage", None), "output_tokens", 0) or 0)
    rid = getattr(response, "id", "")

    assert inp == 123
    assert out == 45
    assert inp + out == 168
    assert rid == "msg_1"


def test_gemini_usage_metadata_extraction_shape():
    # Mirrors GenerateContentResponse.usage_metadata fields we depend on.
    um = SimpleNamespace(prompt_token_count=10, candidates_token_count=7, total_token_count=17)
    response = SimpleNamespace(usage_metadata=um)

    _um = getattr(response, "usage_metadata", None)
    inp = int(getattr(_um, "prompt_token_count", 0) or 0) if _um else 0
    out = int(getattr(_um, "candidates_token_count", 0) or 0) if _um else 0
    tot = int(getattr(_um, "total_token_count", 0) or 0) if _um else 0

    assert inp == 10
    assert out == 7
    assert tot == 17

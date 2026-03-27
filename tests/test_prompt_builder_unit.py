from server.services.prompt_builder import Budget, PromptParts, build_prompt_messages


def test_prompt_builder_trims_optional_context_to_budget():
    parts = PromptParts(
        system="SYS" * 200,
        behavior_rules_appendix="BR" * 2000,
        session_summary="SUM" * 2000,
        retrieved_memory="MEM" * 2000,
        history=[{"role": "user", "content": "H" * 5000}],
        user="U" * 2000,
    )
    messages, meta = build_prompt_messages(
        model="gpt-4o-mini",
        budget=Budget(max_input_tokens=2000, reserve_output_tokens=500),
        parts=parts,
    )

    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert meta["included"]["final_total_tokens"] <= (2000 - 500 + 300)  # allow chat serialization overhead

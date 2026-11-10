from react_loop import never_answering_llm, run


def test_run_terminates_within_step_cap_and_uses_a_tool():
    trace = run(5)
    assert len(trace) <= 5
    assert any(step["action"] for step in trace)


def test_never_answering_model_is_forced_to_stop_at_cap():
    trace = run(2, never_answering_llm)
    assert len(trace) == 2


def test_unknown_tool_feeds_error_observation_back():
    def unknown_tool_llm(prompt: str) -> str:
        return "Thought: Try a missing tool.\nAction: missing[thing]"

    trace = run(2, unknown_tool_llm)
    assert trace[0]["observation"] == "Error: could not parse action"
    assert len(trace) == 2


def test_unparseable_output_feeds_error_observation_back():
    def unparseable_llm(prompt: str) -> str:
        return "Thought: No action here."

    trace = run(2, unparseable_llm)
    assert trace[0]["action"] is None
    assert trace[0]["observation"] == "Error: could not parse action"
    assert len(trace) == 2


def test_cumulative_cost_sums_prompt_and_completion_tokens():
    trace = run(5)
    expected = 0
    for step in trace:
        expected += step["prompt_tokens"] + step["completion_tokens"]
        assert step["cumulative_cost"] == expected

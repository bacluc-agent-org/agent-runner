import reflection_loop


def _recording_fakes(critiques):
    # critiques[i] is the Critique returned on the (i+1)-th call; an extra
    # loop iteration would IndexError here and fail the test
    generate_calls = []
    critique_calls = []

    def generate(task, reflections):
        generate_calls.append({"task": task, "reflections": list(reflections)})
        return f"candidate {len(generate_calls)}"

    def critique(task, candidate):
        critique_calls.append({"task": task, "candidate": candidate})
        return critiques[len(critique_calls) - 1]

    return generate, critique, generate_calls, critique_calls


def test_convergence_on_second_candidate():
    generate, critique, generate_calls, critique_calls = _recording_fakes(
        [
            reflection_loop.Critique(passed=False, feedback="make it rhyme"),
            reflection_loop.Critique(passed=True, feedback="good"),
        ]
    )

    result = reflection_loop.run_loop("write a poem", generate, critique)

    assert result.stop_reason == "converged"
    assert result.attempts == 2
    assert result.candidate == "candidate 2"
    assert len(generate_calls) == 2
    assert result.reflections == ["make it rhyme"]
    assert critique_calls == [
        {"task": "write a poem", "candidate": "candidate 1"},
        {"task": "write a poem", "candidate": "candidate 2"},
    ]


def test_exhausted_budget_with_default_max_retries():
    generate, critique, generate_calls, _ = _recording_fakes(
        [
            reflection_loop.Critique(passed=False, feedback=f"nope {i}")
            for i in range(4)
        ]
    )

    result = reflection_loop.run_loop("write a poem", generate, critique)

    assert result.stop_reason == "exhausted"
    assert result.attempts == 4
    assert result.candidate == "candidate 4"
    assert len(generate_calls) == 4


def test_exhausted_budget_is_parameterized():
    generate, critique, generate_calls, _ = _recording_fakes(
        [
            reflection_loop.Critique(passed=False, feedback=f"nope {i}")
            for i in range(2)
        ]
    )

    result = reflection_loop.run_loop(
        "write a poem", generate, critique, max_retries=1
    )

    assert result.stop_reason == "exhausted"
    assert result.attempts == 2
    assert result.candidate == "candidate 2"
    assert len(generate_calls) == 2


def test_reflections_accumulate_verbatim_in_order():
    feedbacks = ["too vague", "wrong meter", "cliche ending", "still cliche"]
    generate, critique, generate_calls, _ = _recording_fakes(
        [reflection_loop.Critique(passed=False, feedback=f) for f in feedbacks]
    )

    result = reflection_loop.run_loop("write a poem", generate, critique)

    assert generate_calls[0]["reflections"] == []
    assert generate_calls[1]["reflections"] == ["too vague"]
    assert generate_calls[2]["reflections"] == ["too vague", "wrong meter"]
    assert generate_calls[3]["reflections"] == feedbacks[:3]
    assert result.reflections == feedbacks

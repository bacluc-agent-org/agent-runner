#!/usr/bin/env python3
"""Offline reference model of a bounded ReAct loop — not the agent harness.

The harness is the OpenCode CLI installed by
`.github/actions/setup-opencode/action.yml` and run by
`.github/workflows/opencode.yml` (`opencode run --agent coordinator ...`), with
real providers, agents, plugins, tools, skills, permissions and timeouts. This
stdlib-only script models only the control loop: a scripted `fake_llm`, stub
tools, a hard `max_steps` cap, regex action parsing with error-observation
recovery, and approximate token costs (`len(text) // 4`). Only its pytest file
imports it.

The demo exists because the real harness needs a provider, network access, API
keys and minutes per run, so its loop invariants are awkward to regression-test.
Instead this script pins the ReAct contract — Thought -> Action -> Observation,
a hard step cap, parse-error recovery and cumulative cost — in a sub-second
offline pytest and gives newcomers a readable reference for the protocol. It
neither wraps nor replaces the harness.
"""
import re
from collections.abc import Callable

QUESTION = "Question: What is the weather in Zurich?"


def _search(*args: str) -> str:
    return f"search({', '.join(repr(arg) for arg in args)}) -> 'sunny'"


def _lookup(*args: str) -> str:
    return f"lookup({', '.join(repr(arg) for arg in args)}) -> '21°C'"


TOOLS: dict[str, Callable[..., str]] = {
    "search": _search,
    "lookup": _lookup,
}


def fake_llm(prompt: str) -> str:
    if "lookup('temperature') -> '21°C'" in prompt:
        return (
            "Thought: I have the weather and the temperature.\n"
            "Answer: It is sunny and 21°C in Zurich."
        )
    if "search('weather') -> 'sunny'" in prompt:
        return (
            "Thought: The weather is sunny; now check the temperature.\n"
            "Action: lookup[temperature]"
        )
    return "Thought: I need to check the weather first.\nAction: search[weather]"


def never_answering_llm(prompt: str) -> str:
    return "Thought: I still need more information.\nAction: search[weather]"


def parse_action(text: str) -> tuple[str | None, list[str]]:
    match = re.search(r"Action:\s*(\w+)\[(.*?)\]", text)
    if not match:
        return None, []
    return match.group(1), [arg.strip() for arg in match.group(2).split(",") if arg.strip()]


def run(max_steps: int, llm: Callable[[str], str] = fake_llm) -> list[dict]:
    prompt = QUESTION
    trace: list[dict] = []
    step_count = 0
    cumulative_cost = 0

    while True:
        if step_count >= max_steps:
            break
        step_count += 1

        model_output = llm(prompt)
        thought_match = re.search(r"Thought:\s*(.*)", model_output)
        thought = thought_match.group(1).strip() if thought_match else ""
        action_name, args = parse_action(model_output)
        action = f"{action_name}[{', '.join(args)}]" if action_name else None

        if "Answer:" in model_output:
            observation = ""
        elif action_name in TOOLS:
            observation = TOOLS[action_name](*args)
        else:
            observation = "Error: could not parse action"

        prompt_tokens = len(prompt) // 4
        completion_tokens = len(model_output) // 4
        cumulative_cost += prompt_tokens + completion_tokens
        trace.append(
            {
                "step": step_count,
                "thought": thought,
                "action": action,
                "observation": observation,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cumulative_cost": cumulative_cost,
            }
        )

        if "Answer:" in model_output:
            break
        prompt += f"\nObservation: {observation}\n"

    return trace


def demo() -> None:
    max_steps = 5
    trace = run(max_steps)
    for step in trace:
        print(f"step {step['step']}: {step['thought']}")
        print(f"  action: {step['action'] or '-'}")
        print(f"  observation: {step['observation'] or '-'}")
        print(
            f"  tokens: {step['prompt_tokens']} prompt + {step['completion_tokens']} completion"
            f" = {step['cumulative_cost']} cumulative"
        )

    assert len(trace) <= max_steps
    assert any(step["action"] for step in trace)

    trace2 = run(2, never_answering_llm)
    assert len(trace2) == 2
    print(f"self-check passed: {len(trace)} steps, forced termination at {len(trace2)} steps")


def main() -> None:
    demo()


if __name__ == "__main__":
    main()

"""Generate-critique-revise loop with injected model calls. #228 owns this contract."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Critique:
    passed: bool
    feedback: str


@dataclass
class LoopResult:
    candidate: str
    stop_reason: str  # "converged" | "exhausted"
    attempts: int
    reflections: list[str]


def run_loop(
    task: str,
    generate: Callable[[str, list[str]], str],
    critique: Callable[[str, str], Critique],
    max_retries: int = 3,
) -> LoopResult:
    reflections: list[str] = []
    for attempt in range(max_retries + 1):
        candidate = generate(task, reflections)
        c = critique(task, candidate)
        if c.passed:
            return LoopResult(candidate, "converged", attempt + 1, reflections)
        reflections.append(c.feedback)
    return LoopResult(candidate, "exhausted", max_retries + 1, reflections)

#!/usr/bin/env python3
"""Run-outcome classifier — stdlib only, no new dependencies.

Reads the coordinator's final output for an explicit AGENT-RUN-RESULT marker
and classifies the run as work_produced, valid_noop or failure. The loop-step
count is never an input: a substantive run that exits 0 without a result is a
failure, and an intentional no-op stays valid regardless of steps.
"""
import json
import os
import re
import sys

MARKER_RE = re.compile(r"^AGENT-RUN-RESULT:\s*outcome=(\S+)\s+summary=(.*)$", re.MULTILINE)
VALID_MARKER_OUTCOMES = {"work_produced", "no_op"}
NOOP_INTENT_PHRASES = (
    "do not modify",
    "return exactly",
    "smoke test",
    "no-op",
)
SUMMARY_MAX_CHARS = 200

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_summary(text: str) -> str:
    cleaned = ANSI_RE.sub("", text or "")
    cleaned = CONTROL_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("|", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:SUMMARY_MAX_CHARS]


def parse_marker(text: str) -> dict | None:
    """Return the last valid AGENT-RUN-RESULT marker in the output, else None."""
    for outcome, summary in reversed(MARKER_RE.findall(text or "")):
        if outcome in VALID_MARKER_OUTCOMES:
            return {"outcome": outcome, "summary": sanitize_summary(summary)}
    return None


def detect_prompt_intent(prompt: str) -> bool:
    lowered = (prompt or "").lower()
    return any(phrase in lowered for phrase in NOOP_INTENT_PHRASES)


def sanitize_reason(reason: str) -> str:
    cleaned = CONTROL_RE.sub(" ", reason or "")
    cleaned = cleaned.replace("|", " ").strip()
    return cleaned[:100] or "unknown"


def classify_run_outcome(
    coordinator_status: int,
    tee_status: int,
    fatal: str | None,
    output_text: str,
    prompt_text: str,
) -> dict:
    prompt_intent = detect_prompt_intent(prompt_text)
    marker = None
    if fatal:
        outcome, reason = "failure", f"fatal:{sanitize_reason(fatal)}"
    elif coordinator_status == 124:
        outcome, reason = "failure", "timeout"
    elif coordinator_status == -1:
        outcome, reason = "failure", "coordinator_not_run"
    elif coordinator_status != 0 or tee_status != 0:
        outcome, reason = "failure", "process_error"
    else:
        marker = parse_marker(output_text)
        if marker is not None:
            if marker["outcome"] == "work_produced":
                outcome, reason = "work_produced", "result_marker"
            else:
                outcome, reason = "valid_noop", "result_marker"
        elif prompt_intent:
            outcome, reason = "valid_noop", "explicit_noop_intent"
        else:
            outcome, reason = "failure", "missing_result"
    if outcome == "failure":
        loop_failure_mode = "timeout" if reason == "timeout" else "error"
    else:
        loop_failure_mode = "success"
    summary = marker["summary"] if marker is not None else ""
    return {
        "outcome": outcome,
        "reason": reason,
        "summary": summary,
        "prompt_intent": prompt_intent,
        "loop_failure_mode": loop_failure_mode,
    }


def read_fatal_reason(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, errors="replace") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "unknown"
    reason = raw.get("reason") if isinstance(raw, dict) else None
    return sanitize_reason(str(reason)) if reason else "unknown"


def read_output_text(path: str | None) -> str:
    if not path:
        return ""
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def parse_status(value: str | None) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return -1


def write_record(record_path: str, record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(record_path) or ".", exist_ok=True)
        with open(record_path, "w") as f:
            f.write(json.dumps(record, indent=2) + "\n")
    except OSError:
        pass


def append_step_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    try:
        with open(output_path, "a") as f:
            f.write(f"{key}={value}\n")
    except OSError:
        pass


def append_summary_table(record: dict, run_url: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    table = (
        "\n| Run outcome | Value |\n"
        "|---|---|\n"
        f"| Outcome | {record['outcome']} |\n"
        f"| Reason | {record['reason']} |\n"
        f"| Summary | {record['summary']} |\n"
        f"| Run | {run_url} |\n"
    )
    try:
        with open(summary_path, "a") as f:
            f.write(table + "\n")
    except OSError:
        pass


def main() -> int:
    coordinator_status = parse_status(os.environ.get("COORDINATOR_STATUS"))
    tee_status = parse_status(os.environ.get("COORDINATOR_TEE_STATUS"))
    fatal = read_fatal_reason(os.environ.get("FATAL_MARKER"))
    output_text = read_output_text(os.environ.get("COORDINATOR_OUTPUT"))
    prompt_text = os.environ.get("PROMPT", "")
    run_url = os.environ.get("RUN_URL", "")
    branch = os.environ.get("BRANCH", "")

    result = classify_run_outcome(coordinator_status, tee_status, fatal, output_text, prompt_text)
    record = {
        "outcome": result["outcome"],
        "reason": result["reason"],
        "summary": result["summary"],
        "run_url": run_url,
        "branch": branch,
    }

    record_path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "run-outcome.json")
    write_record(record_path, record)

    append_step_output("outcome", result["outcome"])
    append_step_output("reason", result["reason"])
    append_step_output("summary", result["summary"])
    append_step_output("loop_failure_mode", result["loop_failure_mode"])
    append_step_output("record", json.dumps(record))
    append_summary_table(record, run_url)

    print(f"Run outcome: {json.dumps(record)}")
    if result["outcome"] == "failure":
        print(f"::warning::Run outcome: failure ({result['reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

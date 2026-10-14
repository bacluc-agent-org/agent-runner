import subprocess
import threading

import pytest

import subagent_runner


def test_commands_select_opencode_and_explicit_model():
    assert subagent_runner.command("opencode", "alpha/model", "task") == [
        "opencode", "run", "--agent", "coordinator", "--model", "alpha/model", "task"
    ]
    with pytest.raises(ValueError, match="unsupported backend: t3-code"):
        subagent_runner.command("t3-code", "alpha/model", "task")


def test_agents_dispatch_in_parallel(monkeypatch):
    barrier = threading.Barrier(2)
    active = 0
    peak = 0
    lock = threading.Lock()

    def run(agent, backend):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return agent["name"]

    monkeypatch.setattr(subagent_runner, "run_agent", run)
    assert subagent_runner.dispatch([{"name": "a"}, {"name": "b"}], "opencode") == ["a", "b"]
    assert peak == 2


def test_judge_skips_on_failure_and_uses_fallback(monkeypatch):
    calls = []

    def run(agent, backend):
        calls.append(agent["name"])
        if agent["name"] == "bad":
            raise subprocess.CalledProcessError(1, ["fake"])
        return "ok"

    monkeypatch.setattr(subagent_runner, "run_agent", run)
    result = subagent_runner.execute({
        "backend": "opencode",
        "agents": [{"name": "bad", "model": "m"}],
        "judge": {"name": "judge", "model": "j"},
        "fallback": {"name": "fallback", "model": "f"},
    })
    assert result == ["ok"]
    assert "judge" not in calls
    assert calls[-1] == "fallback"


def test_judge_formats_successful_agent_outputs_and_is_optional(monkeypatch, capsys):
    calls = []

    def run(agent, backend):
        calls.append(agent.copy())
        if agent["name"] == "judge":
            raise RuntimeError("judge unavailable")
        return "agent result"

    monkeypatch.setattr(subagent_runner, "run_agent", run)
    result = subagent_runner.execute({
        "backend": "opencode",
        "agents": [{"name": "agent", "model": "m", "prompt": "task"}],
        "judge": {"name": "judge", "model": "j", "prompt": "Choose from:\n{outputs}"},
    })
    assert result == ["agent result"]
    assert calls[1]["prompt"] == "Choose from:\nagent result"
    assert "judge unavailable" in capsys.readouterr().err


def test_missing_cli_fails_clearly(monkeypatch):
    monkeypatch.setattr(subagent_runner.shutil, "which", lambda executable: None)
    with pytest.raises(RuntimeError, match="not found"):
        subagent_runner.run_agent({"model": "m", "prompt": "p"}, "opencode")


def test_run_agent_invokes_explicit_model(monkeypatch):
    calls = []
    monkeypatch.setattr(subagent_runner.shutil, "which", lambda executable: executable)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or subprocess.CompletedProcess(args, 0, stdout="done"))
    assert subagent_runner.run_agent({"model": "alpha/model", "prompt": "task"}, "opencode") == "done"
    assert calls[0][0][calls[0][0].index("--model") + 1] == "alpha/model"


def test_run_agent_isolates_config_and_streams_stderr(monkeypatch):
    calls = []
    monkeypatch.setattr(subagent_runner.shutil, "which", lambda executable: executable)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or subprocess.CompletedProcess(args, 0, stdout="done"))
    monkeypatch.setenv("OPENCODE_CONFIG", "parent.json")
    subagent_runner.run_agent({"model": "m", "prompt": "one", "config": "child.json"}, "opencode")
    subagent_runner.run_agent({"model": "m", "prompt": "two"}, "opencode")
    assert calls[0][1]["env"]["OPENCODE_CONFIG"] == "child.json"
    assert calls[1][1]["env"]["OPENCODE_CONFIG"] == "parent.json"
    assert "capture_output" not in calls[0][1]
    assert calls[0][1]["stdout"] == subprocess.PIPE

import subprocess

import pytest

import subagent_runner


def test_commands_select_backend_and_explicit_model():
    assert subagent_runner.command("opencode", "alpha/model", "task") == [
        "opencode", "run", "--agent", "coordinator", "--model", "alpha/model", "task"
    ]
    assert subagent_runner.command("t3-code", "alpha/model", "task") == [
        "t3", "exec", "--model", "alpha/model", "task"
    ]


def test_agents_dispatch_in_parallel(monkeypatch):
    launched = []

    def run(agent, backend):
        launched.append(agent["name"])
        return agent["name"]

    monkeypatch.setattr(subagent_runner, "run_agent", run)
    assert subagent_runner.dispatch([{"name": "a"}, {"name": "b"}], "opencode") == ["a", "b"]
    assert set(launched) == {"a", "b"}


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

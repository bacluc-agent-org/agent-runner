import json
import re
import subprocess
from types import SimpleNamespace

import pytest

import dump_subagent_transcripts


class TestRenderTranscript:
    def test_renders_text_parts(self):
        session = {
            "info": {"agent": "planner"},
            "messages": [
                {"parts": [{"type": "text", "text": "done reading the docs"}]}
            ],
        }
        assert dump_subagent_transcripts.render_transcript(session, "ses_child1") == [
            "--- Subagent transcript: planner (ses_child1) ---",
            "[planner] done reading the docs",
        ]

    def test_renders_tool_calls_with_truncated_input(self):
        session = {
            "info": {"agent": "planner"},
            "messages": [
                {
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "bash",
                            "state": {
                                "status": "running",
                                "input": {"command": "echo " + "x" * 300},
                            },
                        }
                    ]
                }
            ],
        }
        compact_input = '{"command":"echo ' + "x" * 300 + '"}'
        assert dump_subagent_transcripts.render_transcript(session, "ses_child1") == [
            "--- Subagent transcript: planner (ses_child1) ---",
            "[planner:tool] bash " + compact_input[:200] + "...[truncated]",
        ]

    def test_renders_tool_output_truncated(self):
        session = {
            "info": {"agent": "planner"},
            "messages": [
                {
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "echo hi"},
                                "output": "y" * 700,
                            },
                        }
                    ]
                }
            ],
        }
        assert dump_subagent_transcripts.render_transcript(session, "ses_child1") == [
            "--- Subagent transcript: planner (ses_child1) ---",
            '[planner:tool] bash {"command":"echo hi"}\n  output: '
            + "y" * 500
            + "...[truncated]",
        ]

    def test_skips_reasoning_parts(self):
        session = {
            "info": {"agent": "planner"},
            "messages": [
                {
                    "parts": [
                        {"type": "reasoning", "text": "hidden-chain-of-thought"},
                        {"type": "text", "text": "visible summary"},
                    ]
                }
            ],
        }
        assert dump_subagent_transcripts.render_transcript(session, "ses_child1") == [
            "--- Subagent transcript: planner (ses_child1) ---",
            "[planner] visible summary",
        ]

    def test_skips_task_parts(self):
        session = {
            "info": {"agent": "planner"},
            "messages": [
                {
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "task",
                            "state": {
                                "status": "completed",
                                "metadata": {"sessionId": "ses_grand"},
                                "output": "grandchild done",
                            },
                        },
                        {"type": "text", "text": "delegated work"},
                    ]
                }
            ],
        }
        assert dump_subagent_transcripts.render_transcript(session, "ses_child1") == [
            "--- Subagent transcript: planner (ses_child1) ---",
            "[planner] delegated work",
        ]

    def test_skips_empty_text(self):
        session = {
            "info": {"agent": "planner"},
            "messages": [{"parts": [{"type": "text", "text": ""}, {"type": "text"}]}],
        }
        assert dump_subagent_transcripts.render_transcript(session, "ses_child1") == [
            "--- Subagent transcript: planner (ses_child1) ---",
        ]


class TestMain:
    def test_no_coordinator_session_prints_note(self, monkeypatch, capsys):
        monkeypatch.setenv("COORDINATOR_SESSION_TITLE", "coordinator-run")
        sessions = json.dumps([{"id": "ses_other", "title": "unrelated"}])
        monkeypatch.setattr(
            dump_subagent_transcripts, "run_opencode", lambda *args: sessions
        )
        assert dump_subagent_transcripts.main() == 0
        out = capsys.readouterr().out
        assert "No coordinator session found; skipping subagent transcripts." in out
        assert "::stop-commands::" not in out

    def test_no_subagents_prints_note(self, monkeypatch, capsys):
        monkeypatch.setenv("COORDINATOR_SESSION_TITLE", "coordinator-run")
        sessions = json.dumps([{"id": "ses_root", "title": "coordinator-run"}])
        root_export = {
            "info": {"agent": "coordinator"},
            "messages": [{"parts": [{"type": "text", "text": "all by myself"}]}],
        }

        def fake_run_opencode(*args):
            if args[0] == "session":
                return sessions
            return json.dumps(root_export)

        def fake_run_opencode_to_file(*args, path: str):
            with open(path, "w") as f:
                f.write(fake_run_opencode(*args))

        monkeypatch.setattr(dump_subagent_transcripts, "run_opencode", fake_run_opencode)
        monkeypatch.setattr(
            dump_subagent_transcripts, "run_opencode_to_file", fake_run_opencode_to_file
        )
        assert dump_subagent_transcripts.main() == 0
        out_lines = capsys.readouterr().out.splitlines()
        assert re.fullmatch(r"::stop-commands::[0-9a-f]{64}", out_lines[0])
        token = out_lines[0].removeprefix("::stop-commands::")
        assert out_lines[-1] == f"::{token}::"
        assert "--- Coordinator transcript: coordinator (ses_root) ---" in out_lines
        assert "[coordinator] all by myself" in out_lines
        assert "(No subagents were spawned.)" in out_lines

    def test_renders_child_transcripts_fenced(self, monkeypatch, capsys):
        monkeypatch.setenv("COORDINATOR_SESSION_TITLE", "coordinator-run")
        sessions = json.dumps([{"id": "ses_root", "title": "coordinator-run"}])
        root_export = {
            "info": {"agent": "coordinator"},
            "messages": [
                {
                    "parts": [
                        {"type": "text", "text": "delegating now"},
                        {
                            "type": "tool",
                            "tool": "task",
                            "state": {
                                "status": "completed",
                                "metadata": {"sessionId": "ses_child1"},
                            },
                        },
                    ]
                }
            ],
        }
        child_export = {
            "info": {"agent": "planner"},
            "messages": [
                {"parts": [{"type": "reasoning", "text": "hidden-chain-of-thought"}]},
                {
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "echo " + "x" * 300},
                                "output": "y" * 700,
                            },
                        }
                    ]
                },
                {
                    "parts": [
                        {
                            "type": "tool",
                            "tool": "task",
                            "state": {
                                "status": "completed",
                                "metadata": {"sessionId": "ses_grand"},
                                "output": "grandchild done",
                            },
                        }
                    ]
                },
                {"parts": [{"type": "text", "text": "done reading the docs"}]},
            ],
        }
        exports = {"ses_root": root_export, "ses_child1": child_export}

        def fake_run_opencode(*args):
            if args[0] == "session":
                return sessions
            return json.dumps(exports[args[1]])

        def fake_run_opencode_to_file(*args, path: str):
            with open(path, "w") as f:
                f.write(fake_run_opencode(*args))

        monkeypatch.setattr(dump_subagent_transcripts, "run_opencode", fake_run_opencode)
        monkeypatch.setattr(
            dump_subagent_transcripts, "run_opencode_to_file", fake_run_opencode_to_file
        )
        assert dump_subagent_transcripts.main() == 0
        out = capsys.readouterr().out
        out_lines = out.splitlines()
        assert re.fullmatch(r"::stop-commands::[0-9a-f]{64}", out_lines[0])
        token = out_lines[0].removeprefix("::stop-commands::")
        assert out_lines[-1] == f"::{token}::"
        compact_input = '{"command":"echo ' + "x" * 300 + '"}'
        assert out_lines[1:-1] == [
            "--- Coordinator transcript: coordinator (ses_root) ---",
            "[coordinator] delegating now",
            "--- Subagent transcript: planner (ses_child1) ---",
            "[planner:tool] bash " + compact_input[:200] + "...[truncated]",
            "  output: " + "y" * 500 + "...[truncated]",
            "[planner] done reading the docs",
        ]
        assert "hidden-chain-of-thought" not in out
        assert ":tool] task" not in out
        assert "grandchild" not in out

    def test_export_failure_prints_note(self, monkeypatch, capsys):
        monkeypatch.setenv("COORDINATOR_SESSION_TITLE", "coordinator-run")
        sessions = json.dumps([{"id": "ses_root", "title": "coordinator-run"}])

        def fake_run_opencode(*args):
            if args[0] == "session":
                return sessions
            raise subprocess.CalledProcessError(1, args)

        def fake_run_opencode_to_file(*args, path: str):
            raise subprocess.CalledProcessError(1, args)

        monkeypatch.setattr(dump_subagent_transcripts, "run_opencode", fake_run_opencode)
        monkeypatch.setattr(
            dump_subagent_transcripts, "run_opencode_to_file", fake_run_opencode_to_file
        )
        assert dump_subagent_transcripts.main() == 0
        out_lines = capsys.readouterr().out.splitlines()
        assert re.fullmatch(r"::stop-commands::[0-9a-f]{64}", out_lines[0])
        token = out_lines[0].removeprefix("::stop-commands::")
        assert out_lines[-1] == f"::{token}::"
        assert "--- Coordinator transcript: export failed ---" in out_lines
        assert "(No subagents were spawned.)" in out_lines


class TestRunOpencode:
    def test_runs_opencode_command(self, monkeypatch):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(stdout="out")

        monkeypatch.setattr(dump_subagent_transcripts.subprocess, "run", fake_run)
        assert dump_subagent_transcripts.run_opencode("export", "ses_x") == "out"
        assert len(calls) == 1
        command, kwargs = calls[0]
        assert command == ["opencode", "export", "ses_x"]
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    def test_raises_on_failure(self, monkeypatch):
        def fake_run(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)

        monkeypatch.setattr(dump_subagent_transcripts.subprocess, "run", fake_run)
        with pytest.raises(subprocess.CalledProcessError):
            dump_subagent_transcripts.run_opencode("session", "list")


class TestRegression189:
    def test_run_opencode_to_file_writes_non_empty_output(self, monkeypatch, tmp_path):
        # Regression check for PR #17 / issue #189: run_opencode_to_file must write output
        def fake_run(command, **kwargs):
            out_path = kwargs.get("stdout")
            if out_path is not None:
                out_path.write('{"messages": [{"parts": [{"type":"text","text":"ok"}]}]}')
            return SimpleNamespace(stdout="")

        monkeypatch.setattr(dump_subagent_transcripts.subprocess, "run", fake_run)
        path = str(tmp_path / "mock.json")
        dump_subagent_transcripts.run_opencode_to_file("export", "ses_mock", path=path)
        with open(path) as f:
            content = f.read()
        assert len(content) > 0
        assert "messages" in content

    def test_valid_session_does_not_report_false_no_subagents_or_export_failed(self, monkeypatch, capsys):
        # Fails if script reports false "no subagents spawned" or "export failed" for valid session
        monkeypatch.setenv("COORDINATOR_SESSION_TITLE", "coordinator-run")
        sessions = json.dumps([{"id": "ses_root", "title": "coordinator-run"}])
        root_export = {
            "info": {"agent": "coordinator"},
            "messages": [
                {
                    "parts": [
                        {"type": "text", "text": "delegating"},
                        {
                            "type": "tool",
                            "tool": "task",
                            "state": {
                                "status": "completed",
                                "metadata": {"sessionId": "ses_child1"},
                            },
                        },
                    ]
                }
            ],
        }
        child_export = {
            "info": {"agent": "planner"},
            "messages": [{"parts": [{"type": "text", "text": "done"}]}],
        }
        exports = {"ses_root": root_export, "ses_child1": child_export}

        def fake_run_opencode(*args):
            if args[0] == "session":
                return sessions
            return json.dumps(exports.get(args[1], {}))

        def fake_run_opencode_to_file(*args, path: str):
            with open(path, "w") as f:
                f.write(fake_run_opencode(*args))

        monkeypatch.setattr(dump_subagent_transcripts, "run_opencode", fake_run_opencode)
        monkeypatch.setattr(
            dump_subagent_transcripts, "run_opencode_to_file", fake_run_opencode_to_file
        )
        assert dump_subagent_transcripts.main() == 0
        out = capsys.readouterr().out
        assert "(No subagents were spawned.)" not in out
        assert "--- Coordinator transcript: export failed ---" not in out
        assert "--- Subagent transcript: planner (ses_child1) ---" in out
        assert "[planner] done" in out

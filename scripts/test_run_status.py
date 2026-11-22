import run_status

BASE = {
    "coordinator_status": "0",
    "coordinator_tee_status": "0",
    "coordinator_outcome": "success",
    "select_model_outcome": "success",
    "fatal_reason": "",
    "branch_name": "",
    "pushed_repos": "",
    "timeout_minutes": 120,
}


def classify(**overrides):
    return run_status.classify(**{**BASE, **overrides})


class TestClassify:
    def test_model_selection_failure(self):
        assert classify(select_model_outcome="failure") == (
            "❌ failed (model selection failure; coordinator never started)",
            1,
        )

    def test_fatal_reason(self):
        assert classify(fatal_reason="balance") == ("❌ fatal error: balance", 1)

    def test_cancelled(self):
        assert classify(coordinator_outcome="cancelled") == (
            "⚠️ cancelled (coordinator job cancelled)",
            1,
        )

    def test_missing_coordinator_status(self):
        assert classify(coordinator_status="") == (
            "⚠️ failed (coordinator step aborted before reporting an exit code; outcome=success)",
            1,
        )

    def test_non_digit_coordinator_status(self):
        assert classify(coordinator_status="12x", coordinator_outcome="failure") == (
            "⚠️ failed (coordinator step aborted before reporting an exit code; outcome=failure)",
            1,
        )

    def test_completed(self):
        assert classify() == ("✅ completed", 0)

    def test_timeout_without_delivery(self):
        assert classify(coordinator_status="124", coordinator_outcome="failure") == (
            "⚠️ timed out after 120m (coordinator exit 124)",
            1,
        )

    def test_other_failure_without_delivery(self):
        assert classify(coordinator_status="2", coordinator_outcome="failure") == (
            "⚠️ failed (coordinator exit 2)",
            1,
        )

    def test_timeout_with_branch_name(self):
        assert classify(
            coordinator_status="124",
            coordinator_outcome="failure",
            branch_name="agent-run/307-1",
        ) == ("⚠️ timed out after 120m (coordinator exit 124)", 0)

    def test_timeout_with_pushed_repos(self):
        assert classify(
            coordinator_status="124",
            coordinator_outcome="failure",
            pushed_repos="bacluc-agent/agent-todo",
        ) == ("⚠️ timed out after 120m (coordinator exit 124)", 0)

    def test_other_failure_with_delivery(self):
        assert classify(
            coordinator_status="2",
            coordinator_outcome="failure",
            branch_name="agent-run/307-1",
        ) == ("⚠️ failed (coordinator exit 2)", 0)

    def test_fatal_reason_beats_zero_status(self):
        assert classify(fatal_reason="auth") == ("❌ fatal error: auth", 1)

    def test_select_model_failure_beats_fatal(self):
        assert classify(select_model_outcome="cancelled", fatal_reason="auth") == (
            "❌ failed (model selection cancelled; coordinator never started)",
            1,
        )

    def test_whitespace_delivery_is_undelivered(self):
        assert classify(
            coordinator_status="124",
            coordinator_outcome="failure",
            branch_name=" ",
            pushed_repos="\n\t",
        ) == ("⚠️ timed out after 120m (coordinator exit 124)", 1)

    def test_custom_timeout_label(self):
        assert classify(
            coordinator_status="124",
            coordinator_outcome="failure",
            timeout_minutes=1,
        ) == ("⚠️ timed out after 1m (coordinator exit 124)", 1)


class TestMain:
    def set_env(self, monkeypatch, **overrides):
        env = {
            "COORDINATOR_STATUS": "0",
            "COORDINATOR_TEE_STATUS": "0",
            "COORDINATOR_OUTCOME": "success",
            "SELECT_MODEL_OUTCOME": "success",
            "FATAL_REASON": "",
            "BRANCH_NAME": "",
            "PUSHED_REPOS": "",
            "COORDINATOR_TIMEOUT": "120",
            **overrides,
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

    def test_without_enforce_returns_zero_for_timeout(self, monkeypatch, capsys):
        self.set_env(
            monkeypatch,
            COORDINATOR_STATUS="124",
            COORDINATOR_OUTCOME="failure",
        )
        monkeypatch.setattr("sys.argv", ["run_status.py"])

        assert run_status.main() == 0
        out, err = capsys.readouterr()
        assert out == "⚠️ timed out after 120m (coordinator exit 124)\n"
        assert err == ""

    def test_enforce_returns_classified_code_and_stderr(self, monkeypatch, capsys):
        self.set_env(
            monkeypatch,
            COORDINATOR_STATUS="124",
            COORDINATOR_OUTCOME="failure",
            BRANCH_NAME="agent-run/307-1",
        )
        monkeypatch.setattr("sys.argv", ["run_status.py", "--enforce"])

        assert run_status.main() == 0
        out, err = capsys.readouterr()
        assert out == "⚠️ timed out after 120m (coordinator exit 124)\n"
        assert err == "⚠️ timed out after 120m (coordinator exit 124)\n"

    def test_enforce_returns_failure_code(self, monkeypatch, capsys):
        self.set_env(monkeypatch, SELECT_MODEL_OUTCOME="failure")
        monkeypatch.setattr("sys.argv", ["run_status.py", "--enforce"])

        assert run_status.main() == 1
        out, err = capsys.readouterr()
        assert out == "❌ failed (model selection failure; coordinator never started)\n"
        assert err == out

    def test_custom_timeout_label(self, monkeypatch, capsys):
        self.set_env(
            monkeypatch,
            COORDINATOR_STATUS="124",
            COORDINATOR_OUTCOME="failure",
            COORDINATOR_TIMEOUT="1",
        )
        monkeypatch.setattr("sys.argv", ["run_status.py"])

        assert run_status.main() == 0
        assert capsys.readouterr().out == "⚠️ timed out after 1m (coordinator exit 124)\n"

    def test_blank_timeout_defaults_to_120(self, monkeypatch, capsys):
        self.set_env(
            monkeypatch,
            COORDINATOR_STATUS="124",
            COORDINATOR_OUTCOME="failure",
            COORDINATOR_TIMEOUT="",
        )
        monkeypatch.setattr("sys.argv", ["run_status.py"])

        assert run_status.main() == 0
        assert capsys.readouterr().out == "⚠️ timed out after 120m (coordinator exit 124)\n"

    def test_invalid_timeout_defaults_to_120(self, monkeypatch, capsys):
        self.set_env(
            monkeypatch,
            COORDINATOR_STATUS="124",
            COORDINATOR_OUTCOME="failure",
            COORDINATOR_TIMEOUT="not-a-number",
        )
        monkeypatch.setattr("sys.argv", ["run_status.py"])

        assert run_status.main() == 0
        assert capsys.readouterr().out == "⚠️ timed out after 120m (coordinator exit 124)\n"

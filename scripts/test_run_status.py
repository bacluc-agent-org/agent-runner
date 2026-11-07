import run_status


def test_success():
    assert run_status.classify("0", "0", "failure", "") == "✅ completed"


def test_success_with_tee_failure():
    assert run_status.classify("0", "2", "failure", "") == (
        "✅ completed (log capture failed, tee exit 2)"
    )


def test_timeout():
    assert run_status.classify("124", "0", "failure", "") == (
        "⏱️ timed out (coordinator exit 124)"
    )


def test_nonzero_failure():
    assert run_status.classify("7", "0", "failure", "") == (
        "⚠️ failed (coordinator exit 7)"
    )


def test_fatal_reason():
    assert run_status.classify("1", "0", "failure", "retry_limit") == (
        "❌ fatal error: retry_limit"
    )


def test_cancellation():
    assert run_status.classify("", "", "cancelled", "") == (
        "⚠️ cancelled before the coordinator finished"
    )


def test_skipped_is_fallback_cancellation():
    assert run_status.classify("", "", "skipped", "") == (
        "⚠️ cancelled before the coordinator finished"
    )


def test_missing_status_falls_back_to_outcome():
    assert run_status.classify("", "", "failure", "") == (
        "⚠️ failed (coordinator exit failure)"
    )


def test_main_reads_numeric_statuses(monkeypatch, capsys):
    monkeypatch.setenv("COORDINATOR_STATUS", "0")
    monkeypatch.setenv("COORDINATOR_TEE_STATUS", "3")
    monkeypatch.setenv("COORDINATOR_OUTCOME", "failure")
    monkeypatch.setenv("FATAL_REASON", "")
    assert run_status.main() == 0
    assert capsys.readouterr().out.strip() == (
        "✅ completed (log capture failed, tee exit 3)"
    )

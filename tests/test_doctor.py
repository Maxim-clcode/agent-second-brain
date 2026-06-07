"""Tests for the daily self-diagnostic (Doctor)."""

from d_brain.services.claude_session import AskResult
from d_brain.services.doctor import CheckResult, Doctor


class FakeSession:
    def __init__(self, result: AskResult) -> None:
        self.result = result

    def ask(self, prompt, *, timeout=120, request_id=None) -> AskResult:  # noqa: ANN001
        return self.result


def _ok_check():
    return CheckResult("disk", True, "6 GB free")


def _bad_check():
    return CheckResult("git", False, "push failed")


def test_canary_ok_makes_report_ok():
    sess = FakeSession(AskResult("ok", reply="DBRAIN_OK"))
    rep = Doctor(sess, checks=[_ok_check]).run()
    assert rep.ok
    assert any(c.name == "canary" and c.ok for c in rep.checks)


def test_canary_wrong_reply_fails():
    sess = FakeSession(AskResult("ok", reply="hello?"))
    rep = Doctor(sess, checks=[]).run()
    assert not rep.ok


def test_canary_logged_out_fails_with_reason():
    sess = FakeSession(AskResult("logged_out"))
    rep = Doctor(sess, checks=[]).run()
    canary = next(c for c in rep.checks if c.name == "canary")
    assert not canary.ok
    assert "вход" in canary.detail.lower() or "log" in canary.detail.lower()


def test_failing_local_check_makes_report_not_ok():
    sess = FakeSession(AskResult("ok", reply="DBRAIN_OK"))
    rep = Doctor(sess, checks=[_bad_check]).run()
    assert not rep.ok


def test_report_telegram_green_and_red():
    sess = FakeSession(AskResult("ok", reply="DBRAIN_OK"))
    green = Doctor(sess, checks=[_ok_check]).run().to_telegram()
    assert "🟢" in green
    red = Doctor(sess, checks=[_bad_check]).run().to_telegram()
    assert "🔴" in red
    assert "❌" in red

"""Tests for ClaudeSession orchestration.

The real tmux/subprocess layer is replaced by FakeTmux, which models the
pane as a small state machine and returns scripted capture-pane output.
Clock, sleep, rid generation and liveness are injected so polling, timeout
and stall detection are deterministic and fast.
"""

import subprocess
from pathlib import Path

import pytest

from d_brain.services.claude_session import AskResult, ClaudeSession

READY = (
    "────────────────────\n❯\n────────────────────\n"
    "  hello | Opus 4.8 (1M context) | ~/p\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)
TRUST = (
    " Quick safety check: Is this a project you created or one you trust?\n"
    " ❯ 1. Yes, I trust this folder\n   2. No, exit\n"
)
THINKING = "  ✻ Working…  (esc to interrupt)\n"
RATE = "  You've reached your usage limit. Your limit resets at 3:00 PM.\n❯\n"
LOGGED_OUT = "  Invalid API key · Please run /login to authenticate.\n❯\n"


def _complete(rid: str, reply: str = "PONG") -> str:
    return f"some echo <<<R:{rid}>>> inline\n<<<R:{rid}>>>\n{reply}\n<<<E:{rid}>>>\n❯\n"


def _inline_echo(rid: str) -> str:
    """Echo of the typed prompt: markers appear INLINE (mid-sentence)."""
    return f"> reply, wrap between <<<R:{rid}>>> and <<<E:{rid}>>> markers\n{THINKING}"


class FakeTmux:
    """Callable stand-in for subprocess.run over `tmux ...`."""

    def __init__(self, capture_script: list[str], exists: bool = False) -> None:
        self._captures = list(capture_script)
        self.exists = exists
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):  # noqa: ANN001
        self.calls.append(args)
        sub = args[1] if args and args[0] == "tmux" else args[0]
        out, rc = "", 0
        if sub == "has-session":
            rc = 0 if self.exists else 1
        elif sub == "new-session":
            self.exists = True
        elif sub == "capture-pane":
            out = (
                self._captures.pop(0)
                if len(self._captures) > 1
                else (self._captures[0] if self._captures else "")
            )
        return subprocess.CompletedProcess(args, rc, stdout=out, stderr="")

    def sent_subcommands(self) -> list[str]:
        return [c[1] for c in self.calls if c and c[0] == "tmux"]

    def sent_keys(self) -> list[list[str]]:
        return [c for c in self.calls if len(c) > 1 and c[1] == "send-keys"]

    def enter_count(self) -> int:
        return sum(1 for c in self.sent_keys() if c[-1] == "Enter")


@pytest.fixture
def clock():
    return {"now": 0.0}


def make_session(
    tmp_path: Path,
    fake: FakeTmux,
    clock: dict,
    *,
    rid: str = "rid00001",
    liveness=None,
) -> ClaudeSession:
    def sleep_fn(seconds: float) -> None:
        clock["now"] += seconds

    return ClaudeSession(
        session_name="dbrain_test",
        work_dir=tmp_path / "vault",
        runtime_dir=tmp_path / ".dbrain",
        runner=fake,
        sleep_fn=sleep_fn,
        clock_fn=lambda: clock["now"],
        rid_factory=lambda: rid,
        liveness_fn=liveness or (lambda: clock["now"]),  # default: always "fresh"
        poll_interval=1.0,
        startup_timeout=30.0,
        stall_timeout=10.0,
    )


# ── ensure_session ──────────────────────────────────────────────────────


def test_ensure_session_creates_when_absent(tmp_path, clock):
    fake = FakeTmux([READY], exists=False)
    s = make_session(tmp_path, fake, clock)
    s.ensure_session()
    assert "new-session" in fake.sent_subcommands()
    assert "pipe-pane" in fake.sent_subcommands()
    assert (tmp_path / ".dbrain" / "ready").exists()


def test_ensure_session_noop_when_present(tmp_path, clock):
    fake = FakeTmux([READY], exists=True)
    s = make_session(tmp_path, fake, clock)
    s.ensure_session()
    assert "new-session" not in fake.sent_subcommands()


def test_ensure_session_handles_trust_prompt(tmp_path, clock):
    fake = FakeTmux([TRUST, READY], exists=False)
    s = make_session(tmp_path, fake, clock)
    s.ensure_session()
    assert fake.enter_count() >= 1


def test_ensure_session_does_not_enter_spam_on_trust(tmp_path, clock):
    """Trust persists for several captures; Enter must be debounced to the
    transition, not sent on every poll (M2)."""
    fake = FakeTmux([TRUST, TRUST, TRUST, READY], exists=False)
    s = make_session(tmp_path, fake, clock)
    s.ensure_session()
    assert fake.enter_count() == 1


def test_ensure_session_raises_if_never_ready(tmp_path, clock):
    fake = FakeTmux([THINKING], exists=False)
    s = make_session(tmp_path, fake, clock)
    with pytest.raises(RuntimeError):
        s.ensure_session()


# ── ask ─────────────────────────────────────────────────────────────────


def test_ask_returns_reply_on_completion(tmp_path, clock):
    rid = "abcd0001"
    fake = FakeTmux([READY, THINKING, _complete(rid)], exists=True)
    s = make_session(tmp_path, fake, clock, rid=rid)
    res = s.ask("ping", timeout=60)
    assert isinstance(res, AskResult)
    assert res.ok
    assert res.reply == "PONG"


def test_ask_ignores_inline_echo_contamination(tmp_path, clock):
    """The echoed prompt has inline markers; ask must NOT complete on it,
    only on the real line-anchored answer (H1 at session level)."""
    rid = "echo0001"
    fake = FakeTmux([READY, _inline_echo(rid), _complete(rid)], exists=True)
    s = make_session(tmp_path, fake, clock, rid=rid)
    res = s.ask("ping", timeout=60)
    assert res.reply == "PONG"


def test_ask_detects_rate_limit_without_hanging(tmp_path, clock):
    fake = FakeTmux([RATE], exists=True)
    s = make_session(tmp_path, fake, clock)
    res = s.ask("ping", timeout=60)
    assert res.status == "rate_limited"
    assert not res.ok


def test_ask_detects_logged_out(tmp_path, clock):
    fake = FakeTmux([LOGGED_OUT], exists=True)
    s = make_session(tmp_path, fake, clock)
    res = s.ask("ping", timeout=60)
    assert res.status == "logged_out"


def test_ask_times_out_when_no_reply(tmp_path, clock):
    fake = FakeTmux([READY, THINKING], exists=True)
    # liveness keeps advancing (output flowing) so it's a timeout, not a stall
    s = make_session(tmp_path, fake, clock, liveness=lambda: clock["now"])
    res = s.ask("ping", timeout=3)
    assert res.status == "timeout"


def test_ask_detects_stall_and_interrupts(tmp_path, clock):
    """No new bytes (frozen liveness) longer than stall_timeout → stall, not
    a 20-min hang. Must interrupt and return promptly (C1)."""
    fake = FakeTmux([READY, THINKING], exists=True)
    s = make_session(tmp_path, fake, clock, liveness=lambda: 42.0)  # frozen
    res = s.ask("ping", timeout=600)
    assert res.status == "error"
    assert "stall" in (res.detail or "").lower()
    assert any(c[-1] == "C-c" for c in fake.sent_keys())


def test_ask_returns_error_when_ensure_fails(tmp_path, clock):
    """ensure_session failing must surface as AskResult('error'), never an
    exception out of ask() (C2)."""
    fake = FakeTmux([THINKING], exists=False)  # never becomes READY
    s = make_session(tmp_path, fake, clock)
    res = s.ask("ping", timeout=60)
    assert res.status == "error"


def test_ask_leaves_inflight_orphan_on_timeout(tmp_path, clock):
    """A timed-out prompt is still physically in the pane; inflight marker
    must persist as a stuck-signal (M6)."""
    fake = FakeTmux([READY, THINKING], exists=True)
    s = make_session(tmp_path, fake, clock, liveness=lambda: clock["now"])
    s.ask("ping", timeout=3)
    assert (tmp_path / ".dbrain" / "inflight").exists()


def test_ask_clears_inflight_on_success(tmp_path, clock):
    rid = "abcd0005"
    fake = FakeTmux([READY, _complete(rid)], exists=True)
    s = make_session(tmp_path, fake, clock, rid=rid)
    s.ask("ping", timeout=60)
    assert not (tmp_path / ".dbrain" / "inflight").exists()


def test_ask_sends_prompt_via_buffer_then_enter(tmp_path, clock):
    rid = "abcd0006"
    fake = FakeTmux([READY, _complete(rid)], exists=True)
    s = make_session(tmp_path, fake, clock, rid=rid)
    s.ask("do the thing", timeout=60)
    assert "paste-buffer" in fake.sent_subcommands()
    assert any(c[-1] == "Enter" for c in fake.sent_keys())


# ── health ──────────────────────────────────────────────────────────────


def test_is_healthy_true_when_session_exists(tmp_path, clock):
    fake = FakeTmux([READY], exists=True)
    s = make_session(tmp_path, fake, clock)
    assert s.is_healthy() is True


def test_is_healthy_false_when_absent(tmp_path, clock):
    fake = FakeTmux([READY], exists=False)
    s = make_session(tmp_path, fake, clock)
    assert s.is_healthy() is False


def test_kill_sends_kill_session(tmp_path, clock):
    fake = FakeTmux([READY], exists=True)
    s = make_session(tmp_path, fake, clock)
    s.kill()
    assert "kill-session" in fake.sent_subcommands()

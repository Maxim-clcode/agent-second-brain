"""Tests for the liveness watchdog.

Hang model (after review): a session is hung when its pane state is NOT
serviceable (not READY/RATE_LIMITED/LOGGED_OUT) AND no new bytes have flowed
to pane.log for stall_threshold. This catches a wedged request AND a stuck
startup, never kills a long-but-live task (pane.log keeps growing), and never
kills a healthy idle READY session that merely has an orphan inflight marker.
"""

import os

from d_brain.services.tmux_parse import PaneState
from d_brain.services.watchdog import Watchdog


class FakeSession:
    def __init__(self, *, healthy=True, state=PaneState.READY, recover_ok=True):
        self._healthy = healthy
        self.state = state
        self._recover_ok = recover_ok
        self.recovered = 0

    def is_healthy(self) -> bool:
        return self._healthy

    def current_state(self) -> PaneState:
        return self.state

    def force_recover(self) -> bool:
        self.recovered += 1
        if self._recover_ok:
            self._healthy = True
        return self._recover_ok


def make_wd(tmp_path, session, *, disk_free=10_000_000_000, now=1000.0, alerts=None):
    return Watchdog(
        session,
        runtime_dir=tmp_path,
        disk_free_fn=lambda: disk_free,
        clock_fn=lambda: now,
        alert_fn=(alerts.append if alerts is not None else (lambda m: None)),
        min_disk_bytes=500_000_000,
        stall_threshold=300.0,
        alert_cooldown=3600.0,
    )


def _stale_log(tmp_path, age_from=1000.0, age=400.0):
    p = tmp_path / "pane.log"
    p.write_text("...")
    os.utime(p, (age_from - age, age_from - age))


def _fresh_log(tmp_path, now=1000.0):
    p = tmp_path / "pane.log"
    p.write_text("...")
    os.utime(p, (now - 1, now - 1))


# ── basic states ─────────────────────────────────────────────────────────


def test_healthy_does_nothing(tmp_path):
    sess = FakeSession()
    wd = make_wd(tmp_path, sess)
    assert wd.check_once() == "healthy"
    assert sess.recovered == 0


def test_dead_session_is_recovered(tmp_path):
    sess = FakeSession(healthy=False)
    alerts = []
    wd = make_wd(tmp_path, sess, alerts=alerts)
    assert wd.check_once() == "recovered_dead"
    assert sess.recovered == 1
    assert alerts


def test_rate_limited_is_not_killed(tmp_path):
    sess = FakeSession(state=PaneState.RATE_LIMITED)
    wd = make_wd(tmp_path, sess)
    assert wd.check_once() == "rate_limited"
    assert sess.recovered == 0


def test_logged_out_alerts_without_restart(tmp_path):
    sess = FakeSession(state=PaneState.LOGGED_OUT)
    alerts = []
    wd = make_wd(tmp_path, sess, alerts=alerts)
    assert wd.check_once() == "logged_out"
    assert sess.recovered == 0
    assert alerts


# ── hang detection (state-based) ──────────────────────────────────────────


def test_hung_when_nonserviceable_and_log_stale(tmp_path):
    sess = FakeSession(state=PaneState.UNKNOWN)
    _stale_log(tmp_path)
    wd = make_wd(tmp_path, sess)
    assert wd.check_once() == "recovered_hung"
    assert sess.recovered == 1


def test_stuck_startup_is_recovered(tmp_path):
    sess = FakeSession(state=PaneState.STARTING)
    _stale_log(tmp_path)
    wd = make_wd(tmp_path, sess)
    assert wd.check_once() == "recovered_hung"


def test_long_live_task_not_killed(tmp_path):
    """Non-serviceable (working) but bytes still flowing → alive, not hung."""
    sess = FakeSession(state=PaneState.UNKNOWN)
    _fresh_log(tmp_path)
    wd = make_wd(tmp_path, sess)
    assert wd.check_once() == "healthy"
    assert sess.recovered == 0


def test_idle_ready_with_orphan_inflight_not_killed(tmp_path):
    """CRITICAL regression: a healthy idle READY session with a leftover
    orphan inflight + frozen pane.log must NOT be recovered."""
    sess = FakeSession(state=PaneState.READY)
    (tmp_path / "inflight").write_text("orphan\n")
    _stale_log(tmp_path)
    wd = make_wd(tmp_path, sess)
    assert wd.check_once() == "healthy"
    assert sess.recovered == 0
    assert not (tmp_path / "inflight").exists()  # orphan cleared


def test_recover_deferred_when_lock_busy(tmp_path):
    """force_recover returns False (a live request holds the lock) → report
    deferred, do not claim a restart happened."""
    sess = FakeSession(state=PaneState.UNKNOWN, recover_ok=False)
    _stale_log(tmp_path)
    wd = make_wd(tmp_path, sess)
    assert wd.check_once() == "recover_deferred"


# ── alert debounce ────────────────────────────────────────────────────────


def test_logged_out_alert_debounced_across_ticks(tmp_path):
    sess = FakeSession(state=PaneState.LOGGED_OUT)
    alerts = []
    wd = make_wd(tmp_path, sess, alerts=alerts)
    wd.check_once()
    wd.check_once()
    wd.check_once()
    assert len(alerts) == 1  # not 3


def test_alert_refires_after_recovery(tmp_path):
    sess = FakeSession(state=PaneState.LOGGED_OUT)
    alerts = []
    wd = make_wd(tmp_path, sess, alerts=alerts)
    wd.check_once()  # alert #1
    sess.state = PaneState.READY
    wd.check_once()  # healthy → resets debounce
    sess.state = PaneState.LOGGED_OUT
    wd.check_once()  # alert #2
    assert len(alerts) == 2


# ── disk + status ─────────────────────────────────────────────────────────


def test_disk_full_alerts_and_does_not_restart(tmp_path):
    sess = FakeSession()
    alerts = []
    wd = make_wd(tmp_path, sess, disk_free=1_000_000, alerts=alerts)
    assert wd.check_once() == "disk_full"
    assert sess.recovered == 0
    assert alerts


def test_status_file_written(tmp_path):
    sess = FakeSession()
    wd = make_wd(tmp_path, sess)
    wd.check_once()
    assert (tmp_path / "STATUS.md").exists()

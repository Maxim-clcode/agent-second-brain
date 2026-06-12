"""Contract tests for the install/upgrade scripts.

setup.sh once generated the dead v2 layout (system-level d-brain-* units)
— a fresh install was broken. These pins keep the install path honest:
setup.sh = interactive questions only, upgrade.sh = the single source of
truth for services and health.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent
SETUP = (ROOT / "setup.sh").read_text()
UPGRADE = (ROOT / "upgrade.sh").read_text()


def test_setup_delegates_services_to_upgrade():
    assert "upgrade.sh" in SETUP


def test_setup_has_no_dead_v2_layout():
    assert "d-brain-" not in SETUP  # legacy unit names
    assert "/etc/systemd/system" not in SETUP  # v3 uses systemd --user


def test_setup_login_check_uses_json_not_prose():
    # `claude auth status | grep "Logged in"` broke when the CLI changed
    # its prose; the JSON field is the stable contract.
    assert "loggedIn" in SETUP


def test_setup_asks_timezone():
    assert "TZ=" in SETUP


def test_upgrade_targets_v3():
    assert "v3.1" not in UPGRADE
    assert "dbrain-bot.service" in UPGRADE


def test_legacy_mac_installer_is_gone():
    assert not (ROOT / "install.sh").exists()

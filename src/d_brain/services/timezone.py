"""User timezone detection and storage."""

from pathlib import Path
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "Asia/Vladivostok"


def _tz_file(vault_path: Path) -> Path:
    return vault_path / ".config" / "timezone.txt"


def get_user_tz(vault_path: Path) -> ZoneInfo:
    try:
        tz_name = _tz_file(vault_path).read_text().strip()
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo(_DEFAULT_TZ)


def set_user_tz(vault_path: Path, lat: float, lng: float) -> str:
    from timezonefinder import TimezoneFinder

    tf = TimezoneFinder()
    tz_name = tf.timezone_at(lat=lat, lng=lng) or _DEFAULT_TZ
    _tz_file(vault_path).parent.mkdir(parents=True, exist_ok=True)
    _tz_file(vault_path).write_text(tz_name)
    return tz_name

"""Tests for the extended Settings (tmux-session fields)."""

from pathlib import Path

from d_brain.config import Settings


def _settings(**over):
    base = dict(telegram_bot_token="t", deepgram_api_key="d", _env_file=None)
    base.update(over)
    return Settings(**base)


def test_new_fields_have_safe_defaults():
    s = _settings()
    assert s.tz == "UTC"
    assert s.claude_model == ""  # "" → use the session's default model
    assert isinstance(s.runtime_dir, Path)


def test_admin_chat_id_is_first_allowed_user():
    s = _settings(allowed_user_ids=[111, 222])
    assert s.admin_chat_id == 111


def test_admin_chat_id_none_when_no_users():
    s = _settings(allowed_user_ids=[])
    assert s.admin_chat_id is None


def test_overrides_from_kwargs():
    s = _settings(claude_model="sonnet", tz="Asia/Tashkent")
    assert s.claude_model == "sonnet"
    assert s.tz == "Asia/Tashkent"

"""Tests for the shared session/processor singletons."""

import d_brain.services.runtime as rt
from d_brain.config import Settings


def _settings(tmp_path, **over):
    base = dict(
        telegram_bot_token="t",
        deepgram_api_key="d",
        vault_path=tmp_path / "vault",
        runtime_dir=tmp_path / "rt",
        _env_file=None,
    )
    base.update(over)
    return Settings(**base)


def test_get_session_is_singleton(tmp_path):
    rt.reset()
    s = _settings(tmp_path)
    assert rt.get_session(s) is rt.get_session(s)


def test_get_processor_is_singleton_and_wired_to_session(tmp_path):
    rt.reset()
    s = _settings(tmp_path)
    p = rt.get_processor(s)
    assert rt.get_processor(s) is p
    assert p.session is rt.get_session(s)


def test_session_name_persisted_and_stable(tmp_path):
    rt.reset()
    s = _settings(tmp_path)
    name1 = rt.get_session(s).session_name
    rt.reset()  # drop in-memory singleton; must re-read persisted name
    name2 = rt.get_session(s).session_name
    assert name1 == name2
    assert name1.startswith("dbrain")


def test_explicit_session_name_used(tmp_path):
    rt.reset()
    s = _settings(tmp_path, brain_session_name="dbrain_fixed")
    assert rt.get_session(s).session_name == "dbrain_fixed"

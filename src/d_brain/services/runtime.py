"""Process-wide singletons for the shared Claude session.

The bot, the daily pipeline and the watchdog must all talk to ONE persistent
session. This module builds it lazily from Settings and hands the same
instance to every caller. An asyncio lock serializes ask() calls within the
bot process (the cross-process flock in ClaudeSession is the real mutex; this
just avoids piling up blocked worker threads).
"""

import asyncio
import uuid

from d_brain.config import Settings
from d_brain.services.claude_session import ClaudeSession, RouterSession
from d_brain.services.processor import ClaudeProcessor

_session: ClaudeSession | None = None
_processor: ClaudeProcessor | None = None
_ask_lock = asyncio.Lock()


def reset() -> None:
    """Drop the singletons (tests only)."""
    global _session, _processor
    _session = None
    _processor = None


def _persisted_name(settings: Settings) -> str:
    if settings.brain_session_name:
        return settings.brain_session_name
    # Randomize per install (fingerprint hygiene) and persist so restarts
    # reuse the same tmux session name.
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    name_file = settings.runtime_dir / "brain.name"
    if name_file.exists():
        return name_file.read_text().strip()
    name = f"dbrain_{uuid.uuid4().hex[:8]}"
    name_file.write_text(name + "\n")
    return name


def get_session(settings: Settings):
    """Return the shared session: interactive ClaudeSession, or RouterSession
    when the escape hatch (DBRAIN_MODE=router) is engaged."""
    global _session
    if _session is not None:
        return _session
    if settings.dbrain_mode == "router":
        _session = RouterSession(
            base_url=settings.anthropic_base_url,
            auth_token=settings.anthropic_auth_token,
            work_dir=settings.vault_path,
            model=settings.claude_model or None,
        )
        return _session
    project_root = settings.vault_path.parent
    mcp = project_root / "mcp-config.json"
    brain_prompt = project_root / "deploy" / "brain-system.md"
    _session = ClaudeSession(
        session_name=_persisted_name(settings),
        work_dir=settings.vault_path,
        runtime_dir=settings.runtime_dir,
        mcp_config=mcp if mcp.exists() else None,
        system_prompt_file=brain_prompt if brain_prompt.exists() else None,
        model=settings.claude_model or None,
    )
    return _session


def get_processor(settings: Settings) -> ClaudeProcessor:
    global _processor
    if _processor is None:
        _processor = ClaudeProcessor(
            settings.vault_path,
            settings.todoist_api_key,
            session=get_session(settings),
        )
    return _processor


def get_ask_lock() -> asyncio.Lock:
    return _ask_lock

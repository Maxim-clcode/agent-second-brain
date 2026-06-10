"""Unified private chat handler with persistent Claude sessions.

Voice + text only (v3.0): replaces the legacy split handlers for private chats.
Every message is saved to daily (safety net) and routed IMMEDIATELY through
ChatSessionManager for Claude to process and respond — no debounce buffer.
"""

import asyncio
import html
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from d_brain.bot.formatters import send_response
from d_brain.config import get_settings
from d_brain.services.chat_session import ChatSessionManager
from d_brain.services.session import SessionStore
from d_brain.services.storage import VaultStorage
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="chat")
logger = logging.getLogger(__name__)

# Only handle private chats
router.message.filter(F.chat.type == ChatType.PRIVATE)

MAX_RESPONSE_LENGTH = 4096

# Slash commands split by BEHAVIOR, not by the leading "/":
# - control: client-side Claude Code commands — no model turn, fire-and-forget
# - tui: interactive full-screen UIs — undrivable through a typed pane
# - everything else (incl. /skill-name) is a normal model turn → marker path
_CONTROL = {"/clear", "/compact", "/model"}
_TUI_ONLY = {"/agents", "/config", "/login"}

_manager: ChatSessionManager | None = None


def classify_command(text: str) -> str:
    """'control' | 'tui' | 'turn' for an incoming chat text."""
    if not text.startswith("/"):
        return "turn"
    head = text.split(maxsplit=1)[0]
    if head in _CONTROL:
        return "control"
    if head in _TUI_ONLY:
        return "tui"
    return "turn"


_STOP_WORDS = {"/stop", "stop", "стоп"}


def classify_concurrent_input(text: str, turn_active: bool) -> str:
    """'ask' | 'steer' | 'interrupt' — what to do with input that arrives
    while the agent may be busy. Plain text during an active turn STEERS it
    (injected mid-turn); a stop word interrupts; otherwise a normal turn."""
    if not turn_active:
        return "ask"
    if text.strip().lower() in _STOP_WORDS:
        return "interrupt"
    return "steer"


def _get_manager() -> ChatSessionManager:
    """Lazy-init ChatSessionManager singleton."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        settings = get_settings()
        _manager = ChatSessionManager(settings.vault_path)
    return _manager


async def _dispatch_text(bot: Bot, chat_id: int, user_id: int, text: str) -> None:
    """Route a text by behavior: control → fire-and-forget; tui → hint;
    normal turn (incl. /skill-name) → session via the marker path."""
    kind = classify_command(text)
    if kind == "control":
        await _get_manager().send_control(text)
        await bot.send_message(
            chat_id, f"⌨️ <code>{html.escape(text)}</code> отправлена в сессию."
        )
        return
    if kind == "tui":
        await bot.send_message(
            chat_id,
            "Эта команда открывает интерактивный интерфейс — доступно только "
            "через <code>dbrain attach</code> на сервере.",
        )
        return

    manager = _get_manager()
    mode = classify_concurrent_input(text, manager.is_turn_active())
    if mode == "interrupt":
        await manager.interrupt()
        await bot.send_message(chat_id, "⏹ Останавливаю текущий ответ.")
        return
    if mode == "steer":
        await manager.steer(text)
        await bot.send_message(chat_id, "↪️ Передал в текущую задачу.")
        return
    await _process_and_reply(bot, chat_id, user_id, text)


async def _process_and_reply(bot: Bot, chat_id: int, user_id: int, prompt: str) -> None:
    """Send the prompt to the shared session and deliver the reply."""
    typing_task = asyncio.create_task(_typing_loop(bot, chat_id))
    try:
        manager = _get_manager()
        response = await manager.send_message(user_id, prompt)

        if response:
            await send_response(bot, chat_id, response)
        else:
            logger.warning(
                "Empty response from Claude for user %d, retrying...", user_id
            )
            # Retry once before giving up — don't reset session on first empty
            response = await manager.send_message(user_id, prompt)
            if response:
                await send_response(bot, chat_id, response)
            else:
                logger.warning("Empty response after retry for user %d", user_id)
                await bot.send_message(
                    chat_id,
                    "Claude не ответил дважды. Повтори сообщение.",
                )

    except Exception as e:
        logger.exception("Chat session error for user %d", user_id)
        error_text = f"Error: {html.escape(str(e)[:200])}"
        try:
            await bot.send_message(chat_id, error_text)
        except Exception:
            logger.exception("Failed to send error message")
    finally:
        typing_task.cancel()


async def _typing_loop(bot: Bot, chat_id: int) -> None:
    """Send typing action every 4 seconds while processing."""
    try:
        while True:
            await bot.send_chat_action(chat_id, "typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


# --- Handlers ---


@router.message(F.voice)
async def handle_chat_voice(message: Message, bot: Bot) -> None:
    """Handle voice messages in private chat."""
    if not message.voice or not message.from_user:
        return

    settings = get_settings()
    storage = VaultStorage(settings.vault_path)
    transcriber = DeepgramTranscriber(settings.deepgram_api_key)

    try:
        file = await bot.get_file(message.voice.file_id)
        if not file.file_path:
            await message.answer("Failed to download voice")
            return

        file_bytes = await bot.download_file(file.file_path)
        if not file_bytes:
            await message.answer("Failed to download voice")
            return

        transcript = await transcriber.transcribe(file_bytes.read())
        if not transcript:
            await message.answer("Could not transcribe audio")
            return

        # Safety net: save to daily
        timestamp = datetime.fromtimestamp(message.date.timestamp())
        storage.append_to_daily(transcript, timestamp, "[voice]")

        # Log to session
        session = SessionStore(settings.vault_path)
        session.append(
            message.from_user.id,
            "voice",
            text=transcript,
            duration=message.voice.duration,
            msg_id=message.message_id,
        )

        await _process_and_reply(
            bot, message.chat.id, message.from_user.id, f"[voice] {transcript}"
        )

    except Exception as e:
        logger.exception("Error processing voice in chat")
        await message.answer(f"Error: {e}")


@router.message(F.text)
async def handle_chat_text(message: Message, bot: Bot) -> None:
    """Handle text messages in private chat.

    Bot-level commands (/start, /help, …) are intercepted by routers
    registered earlier; anything that reaches here — including Claude Code
    slash commands and /skill-name invocations — is dispatched by behavior.
    """
    if not message.text or not message.from_user:
        return

    settings = get_settings()
    storage = VaultStorage(settings.vault_path)

    # Safety net: save to daily
    timestamp = datetime.fromtimestamp(message.date.timestamp())
    storage.append_to_daily(message.text, timestamp, "[text]")

    # Log to session
    session = SessionStore(settings.vault_path)
    session.append(
        message.from_user.id,
        "text",
        text=message.text,
        msg_id=message.message_id,
    )

    await _dispatch_text(bot, message.chat.id, message.from_user.id, message.text)

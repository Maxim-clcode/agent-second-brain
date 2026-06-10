"""Tests for the unified chat handler (v3.0: immediate routing, no debounce)."""

import asyncio


class FakeManager:
    def __init__(self, reply="ответ"):
        self.reply = reply
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, user_id: int, prompt: str) -> str:
        self.sent.append((user_id, prompt))
        return self.reply


class FakeBot:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))

    async def send_chat_action(self, chat_id, action):
        pass


def test_text_message_routed_immediately(monkeypatch):
    """v3.0: an incoming message reaches the session manager immediately —
    no debounce buffer, no delayed flush."""
    from d_brain.bot.handlers import chat

    mgr = FakeManager(reply="<b>готово</b>")
    monkeypatch.setattr(chat, "_get_manager", lambda: mgr)
    bot = FakeBot()

    asyncio.run(chat._process_and_reply(bot, chat_id=10, user_id=1, prompt="привет"))

    assert mgr.sent == [(1, "привет")]
    assert bot.messages and "готово" in bot.messages[0][1]


def test_no_debounce_infrastructure_left():
    """The debounce buffer is fully removed."""
    from d_brain.bot.handlers import chat

    for name in ("DEBOUNCE_SECONDS", "DebounceBuffer", "_add_to_buffer", "_debounce_flush", "_buffers"):
        assert not hasattr(chat, name), f"zombie debounce symbol: {name}"

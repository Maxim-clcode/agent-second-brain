"""Tests for ChatSessionManager — chat messages routed to the shared session."""

import asyncio

from d_brain.services.claude_session import AskResult


class FakeSession:
    def __init__(self, result: AskResult) -> None:
        self.result = result
        self.prompts: list[str] = []
        self.cleared = 0

    def ask(self, prompt: str, **kwargs) -> AskResult:
        self.prompts.append(prompt)
        return self.result

    def clear(self) -> None:
        self.cleared += 1


def _manager(tmp_path, result: AskResult):
    from d_brain.services.chat_session import ChatSessionManager

    return ChatSessionManager(tmp_path, session=FakeSession(result))


def test_send_message_returns_reply_on_ok(tmp_path):
    m = _manager(tmp_path, AskResult("ok", reply="привет"))
    reply = asyncio.run(m.send_message(1, "здравствуй"))
    assert reply == "привет"
    assert m._session.prompts == ["здравствуй"]


def test_send_message_maps_rate_limited(tmp_path):
    m = _manager(tmp_path, AskResult("rate_limited"))
    reply = asyncio.run(m.send_message(1, "x"))
    assert "Лимит" in reply


def test_reset_clears_live_session(tmp_path):
    m = _manager(tmp_path, AskResult("ok", reply=""))
    m.reset(1)
    assert m._session.cleared == 1

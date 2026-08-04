"""Tests for the 'backlog empty' guard — Bug #6.

Three layers:
1. Prompt contains the prohibition at the TOP (before any steps)
2. _has_forbidden_backlog_phrase() catches all variants
3. _sanitize_backlog_phrases() hard-replaces as last resort
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from d_brain.bot.handlers.chat import (
    _has_forbidden_backlog_phrase,
    _sanitize_backlog_phrases,
    _FORBIDDEN_BACKLOG_PHRASES,
)
from d_brain.bot.handlers.buttons import _build_plan_prompt, _build_summary_prompt


# ── Prompt layer ────────────────────────────────────────────────────────────


class TestPlanPromptContainsGuard:
    """_build_plan_prompt must warn Claude at the TOP, before any steps."""

    def _prompt(self, backlog_count=None):
        return _build_plan_prompt(
            day_label="сегодня",
            date_str="04.08.2026",
            date_iso="2026-08-04",
            occupied_slots="",
            backlog_count=backlog_count,
        )

    def test_prohibition_appears_before_step1(self):
        prompt = self._prompt(backlog_count=5)
        prohibition_pos = prompt.lower().find("запрещена категорически")
        step1_pos = prompt.find("ШАГ 1")
        assert prohibition_pos != -1, "Запрет должен быть в промпте"
        assert prohibition_pos < step1_pos, (
            "Запрет должен стоять ДО ШАГ 1, чтобы Claude его не забыл"
        )

    def test_prohibition_present_without_count(self):
        prompt = self._prompt(backlog_count=None)
        assert "ЗАПРЕЩЕНО" in prompt or "запрещено" in prompt.lower()

    def test_backlog_count_mentioned_when_provided(self):
        prompt = self._prompt(backlog_count=12)
        assert "12" in prompt, "Конкретное число задач должно быть в промпте"

    def test_prompt_does_not_assert_backlog_is_empty(self):
        """Промпт не должен утверждать что бэклог пуст (фразы в запрете — допустимы как примеры).
        Проверяем что фразы есть ТОЛЬКО в контексте запрета, а не как утверждение."""
        prompt = self._prompt(backlog_count=5)
        lower = prompt.lower()
        # Backlog-empty phrases must appear only inside the prohibition block (before "ШАГ 1")
        prohibition_end = lower.find("выполни строго по шагам")
        after_prohibition = lower[prohibition_end:]
        assert "бэклог пуст" not in after_prohibition, (
            "После блока запрета фраза 'бэклог пуст' не должна появляться"
        )
        assert "бэклог чист" not in after_prohibition, (
            "После блока запрета фраза 'бэклог чист' не должна появляться"
        )


class TestSummaryPromptContainsGuard:
    """_build_summary_prompt must also prohibit backlog-empty phrases."""

    def _prompt(self, backlog_count=None):
        return _build_summary_prompt(
            today_str="04.08.2026 (пн)",
            today_iso="2026-08-04",
            next_day_str="05.08.2026 (вт)",
            next_day_iso="2026-08-05",
            backlog_count=backlog_count,
        )

    def test_prohibition_present(self):
        prompt = self._prompt(backlog_count=3)
        lower = prompt.lower()
        assert "запрещено" in lower or "запрещена" in lower

    def test_backlog_count_mentioned(self):
        prompt = self._prompt(backlog_count=7)
        assert "7" in prompt


# ── Detection layer ──────────────────────────────────────────────────────────


class TestForbiddenPhraseDetection:
    """_has_forbidden_backlog_phrase must catch all variants Claude might write."""

    @pytest.mark.parametrize("text", [
        "Бэклог чист, задач нет",
        "бэклог пуст на сегодня",
        "Беклог пуст.",
        "беклог чист",
        "Backlog пуст",
        "в бэклоге нет задач",
        "бэклог пустой",
        "Бэклог — пуст",
        "Бэклог сейчас пуст",
        "БЭКЛОГ ЧИСТ",  # uppercase
        "бЭклОг пУст",  # mixed case
    ])
    def test_detects_forbidden(self, text):
        assert _has_forbidden_backlog_phrase(text), f"Не поймал: {text!r}"

    @pytest.mark.parametrize("text", [
        "В бэклоге есть активные задачи",
        "Добавил задачу в бэклог",
        "Бэклог содержит 5 задач",
        "Задачи из бэклога перенесены в спринт",
        "Бэклог проверен, там 3 задачи",
        "Посмотрел бэклог — нашёл кандидатов",
    ])
    def test_allows_valid(self, text):
        assert not _has_forbidden_backlog_phrase(text), f"Ложное срабатывание: {text!r}"


# ── Sanitizer layer ──────────────────────────────────────────────────────────


class TestSanitizer:
    """_sanitize_backlog_phrases must replace forbidden phrases in final text."""

    def test_replaces_backlog_chisty(self):
        result = _sanitize_backlog_phrases("Бэклог чист, можно отдыхать")
        assert "чист" not in result.lower() or "бэклог чист" not in result.lower()
        assert "активные задачи" in result

    def test_replaces_backlog_pust(self):
        result = _sanitize_backlog_phrases("К сожалению, бэклог пуст")
        assert "бэклог пуст" not in result.lower()

    def test_replaces_beklог(self):
        result = _sanitize_backlog_phrases("беклог пуст сегодня")
        assert "беклог пуст" not in result.lower()

    def test_preserves_rest_of_text(self):
        original = "План на день готов. Бэклог пуст. Удачи!"
        result = _sanitize_backlog_phrases(original)
        assert "План на день готов" in result
        assert "Удачи" in result

    def test_no_forbidden_after_sanitize(self):
        texts = [
            "бэклог чист",
            "бэклог пуст",
            "беклог пустой",
            "backlog пуст",
        ]
        for t in texts:
            result = _sanitize_backlog_phrases(t)
            assert not _has_forbidden_backlog_phrase(result), (
                f"После sanitize всё ещё есть запрещённая фраза: {result!r}"
            )


# ── Self-correction integration ──────────────────────────────────────────────


class TestSelfCorrectionLoop:
    """_process_and_reply must trigger self-correction when Claude slips."""

    def test_self_correction_triggered(self):
        """Simulate: first response has forbidden phrase, correction is clean."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        calls = []

        async def fake_send(user_id, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return "Бэклог чист, задач нет"
            return "В бэклоге 5 задач, но они не подходят для сегодня по приоритету"

        async def run():
            from d_brain.bot.handlers.chat import _process_and_reply
            fake_bot = AsyncMock()
            fake_bot.send_message = AsyncMock()

            with patch("d_brain.bot.handlers.chat._get_manager") as mock_mgr:
                manager = AsyncMock()
                manager.send_message = AsyncMock(side_effect=fake_send)
                mock_mgr.return_value = manager

                with patch("d_brain.bot.handlers.chat.send_response") as mock_send:
                    await _process_and_reply(fake_bot, 123, 456, "собери план")
                    sent_text = mock_send.call_args[0][2]
                    assert not _has_forbidden_backlog_phrase(sent_text), (
                        f"После самоисправления не должно быть запрещённой фразы: {sent_text!r}"
                    )
                    assert len(calls) == 2, "Должен был запросить самоисправление"

        asyncio.run(run())

    def test_hard_sanitize_when_self_correction_fails(self):
        """Simulate: both responses have forbidden phrase — sanitizer kicks in."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        async def fake_send(user_id, prompt):
            return "Бэклог чист"  # always returns forbidden

        async def run():
            from d_brain.bot.handlers.chat import _process_and_reply
            fake_bot = AsyncMock()

            with patch("d_brain.bot.handlers.chat._get_manager") as mock_mgr:
                manager = AsyncMock()
                manager.send_message = AsyncMock(side_effect=fake_send)
                mock_mgr.return_value = manager

                with patch("d_brain.bot.handlers.chat.send_response") as mock_send:
                    await _process_and_reply(fake_bot, 123, 456, "собери план")
                    sent_text = mock_send.call_args[0][2]
                    assert not _has_forbidden_backlog_phrase(sent_text), (
                        f"Sanitizer должен был убрать фразу: {sent_text!r}"
                    )

        asyncio.run(run())

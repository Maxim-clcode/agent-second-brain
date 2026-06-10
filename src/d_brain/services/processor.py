"""Claude processing service.

Migrated from spawning `claude --print -p` per call to driving a shared
persistent interactive ClaudeSession (tmux). This keeps usage on the
subscription after the 2026-06-15 Agent SDK billing change. The public
methods and their {report|error} return shape are unchanged so the bot
handlers don't need to change.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Any

from d_brain.services.claude_session import DEFAULT_TIMEOUT, AskResult, ClaudeSession
from d_brain.services.session import SessionStore

logger = logging.getLogger(__name__)

# User-facing messages for non-ok session outcomes (Telegram HTML).
_STATUS_MESSAGES = {
    "rate_limited": "⏳ <b>Лимит подписки исчерпан.</b> Вернусь, когда он обновится.",
    "logged_out": (
        "🔑 <b>Сессии нужен повторный вход.</b> Админу: <code>dbrain login</code>."
    ),
    "timeout": "⌛ <b>Превышено время ожидания ответа.</b> Попробуйте ещё раз.",
    "error": "❌ <b>Ошибка сессии.</b> Попробуйте позже.",
}


class ClaudeProcessor:
    """Builds prompts and runs them through the shared ClaudeSession."""

    def __init__(
        self,
        vault_path: Path,
        session: ClaudeSession | None = None,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.session = session

    # ── result mapping ───────────────────────────────────────────────

    def _to_report(self, res: AskResult) -> dict[str, Any]:
        if res.ok:
            return {"report": res.reply or "", "processed_entries": 1}
        logger.error("session returned %s: %s", res.status, res.detail)
        return {
            "error": _STATUS_MESSAGES.get(res.status, res.detail or "session error"),
            "processed_entries": 0,
        }

    def _ask(self, prompt: str) -> dict[str, Any]:
        if self.session is None:
            return {"error": "session not configured", "processed_entries": 0}
        return self._to_report(self.session.ask(prompt, timeout=DEFAULT_TIMEOUT))

    # ── content helpers (unchanged) ──────────────────────────────────

    def _load_skill_content(self) -> str:
        skill_path = self.vault_path / ".claude/skills/dbrain-processor/SKILL.md"
        return skill_path.read_text() if skill_path.exists() else ""

    def _get_session_context(self, user_id: int) -> str:
        if user_id == 0:
            return ""
        session = SessionStore(self.vault_path)
        today_entries = session.get_today(user_id)
        if not today_entries:
            return ""
        lines = ["=== TODAY'S SESSION ==="]
        for entry in today_entries[-10:]:
            ts = entry.get("ts", "")[11:16]
            entry_type = entry.get("type", "unknown")
            text = entry.get("text", "")[:80]
            if text:
                lines.append(f"{ts} [{entry_type}] {text}")
        lines.append("=== END SESSION ===\n")
        return "\n".join(lines)

    # ── public operations ────────────────────────────────────────────

    def process_daily(self, day: date | None = None) -> dict[str, Any]:
        if day is None:
            day = date.today()
        daily_file = self.vault_path / "daily" / f"{day.isoformat()}.md"
        if not daily_file.exists():
            logger.warning("No daily file for %s", day)
            return {"error": f"No daily file for {day}", "processed_entries": 0}

        skill_content = self._load_skill_content()
        prompt = f"""Сегодня {day}. Выполни ежедневную обработку.

=== SKILL INSTRUCTIONS ===
{skill_content}
=== END SKILL ===

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ## , no ```, no tables
- Start directly with 📊 <b>Обработка за {day}</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- If entries already processed, return status report in same HTML format"""
        return self._ask(prompt)

    def execute_prompt(self, user_prompt: str, user_id: int = 0) -> dict[str, Any]:
        today = date.today()
        session_context = self._get_session_context(user_id)
        prompt = f"""Ты - персональный ассистент d-brain.

CONTEXT:
- Текущая дата: {today}
- Vault path: {self.vault_path}

{session_context}USER REQUEST:
{user_prompt}

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ##, no ```, no tables, no -
- Start with emoji and <b>header</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- Be concise - Telegram has 4096 char limit

EXECUTION:
1. Analyze the request
2. Read/write vault files as needed
3. Return HTML status report with results"""
        return self._ask(prompt)

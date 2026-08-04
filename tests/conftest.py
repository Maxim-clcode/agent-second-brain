"""Shared test fixtures and environment setup."""
import os
import pytest

# Stub environment variables required by the module-level code in handlers
_ENV_STUBS = {
    "NOTION_TOKEN": "test-notion-token",
    "TICKTICK_TOKEN": "test-ticktick-token",
    "TELEGRAM_BOT_TOKEN": "0000000000:test-telegram-token",
    "ALLOWED_USER_IDS": "[123456]",
    "TELEGRAM_TOKEN": "test-telegram-token",
    "TELEGRAM_CHAT_ID": "123456",
}


def pytest_configure(config):
    """Set stub env vars before any module is imported."""
    for key, value in _ENV_STUBS.items():
        os.environ.setdefault(key, value)

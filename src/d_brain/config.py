"""Application configuration using Pydantic Settings."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(description="Telegram Bot API token")
    deepgram_api_key: str = Field(description="Deepgram API key for transcription")
    vault_path: Path = Field(
        default=Path("./vault"),
        description="Path to Obsidian vault directory",
    )
    allowed_user_ids: list[int] = Field(
        default_factory=list,
        description="List of Telegram user IDs allowed to use the bot",
    )
    allow_all_users: bool = Field(
        default=False,
        description="Whether to allow access to all users (security risk!)",
    )

    # ── persistent tmux session ──────────────────────────────────────
    runtime_dir: Path = Field(
        default_factory=lambda: Path.home() / ".dbrain",
        description="Runtime dir for locks, pane.log, ready/inflight flags",
    )
    brain_session_name: str = Field(
        default="",
        description="tmux session name (empty → generated & persisted per install)",
    )
    claude_model: str = Field(
        default="",
        description="Model for the session (empty = Claude Code default)",
    )
    tz: str = Field(default="UTC", description="Timezone for timers/reports")

    @property
    def admin_chat_id(self) -> int | None:
        """First allowed user — destination for health alerts / reports."""
        return self.allowed_user_ids[0] if self.allowed_user_ids else None

    @property
    def daily_path(self) -> Path:
        """Path to daily notes directory."""
        return self.vault_path / "daily"

    @property
    def attachments_path(self) -> Path:
        """Path to attachments directory."""
        return self.vault_path / "attachments"

    @property
    def thoughts_path(self) -> Path:
        """Path to thoughts directory."""
        return self.vault_path / "thoughts"


def get_settings() -> Settings:
    """Get application settings instance."""
    return Settings()

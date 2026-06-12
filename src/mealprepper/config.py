from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3.2", alias="OLLAMA_MODEL")
    ollama_embedding_model: str = Field(default="nomic-embed-text", alias="OLLAMA_EMBEDDING_MODEL")
    ollama_timeout: float = Field(default=120.0, alias="OLLAMA_TIMEOUT")
    max_context_chars: int = Field(default=12000, alias="MAX_CONTEXT_CHARS")

    mealprepper_data_dir: Path = Field(default=Path("./data"), alias="MEALPREPPER_DATA_DIR")
    default_timezone: str = Field(default="America/New_York", alias="DEFAULT_TIMEZONE")

    comms_backend: str = Field(
        default="console",
        validation_alias=AliasChoices("COMMS_BACKEND", "SMS_BACKEND"),
    )
    slack_webhook_url: str = Field(default="", alias="SLACK_WEBHOOK_URL")
    slack_bot_token: str = Field(default="", alias="SLACK_BOT_TOKEN")
    slack_app_token: str = Field(default="", alias="SLACK_APP_TOKEN")
    slack_channel_id: str = Field(default="", alias="SLACK_CHANNEL_ID")
    slack_client_id: str = Field(default="", alias="SLACK_CLIENT_ID")
    slack_client_secret: str = Field(default="", alias="SLACK_CLIENT_SECRET")
    slack_oauth_redirect_uri: str = Field(default="", alias="SLACK_OAUTH_REDIRECT_URI")
    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    discord_bot_token: str = Field(default="", alias="DISCORD_BOT_TOKEN")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    apple_shortcuts_webhook_url: str = Field(default="", alias="APPLE_SHORTCUTS_WEBHOOK_URL")

    approval_required: bool = Field(default=True, alias="APPROVAL_REQUIRED")
    daily_reminder_hour: int = Field(default=7, alias="DAILY_REMINDER_HOUR")

    @property
    def project_root(self) -> Path:
        return _project_root()

    @property
    def config_dir(self) -> Path:
        return self.project_root / "config"

    @property
    def data_dir(self) -> Path:
        path = self.mealprepper_data_dir
        if not path.is_absolute():
            path = self.project_root / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mealprepper.db"

    def load_yaml(self, name: str) -> dict[str, Any]:
        path = self.config_dir / name
        if not path.exists():
            return {}
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def merged_config(self) -> dict[str, Any]:
        defaults = self.load_yaml("default.yaml")
        family = self.load_yaml("family.yaml")
        return {**defaults, **family}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

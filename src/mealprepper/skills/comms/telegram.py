from __future__ import annotations

import logging

from mealprepper.config import Settings, get_settings
from mealprepper.skills.comms.base import CommsBackend
from mealprepper.skills.comms.http_utils import post_json, split_message

logger = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 3900


class TelegramCommsBackend(CommsBackend):
    """Send messages via the Telegram Bot API (BotFather token + chat id)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(self, to: str, body: str) -> bool:
        token = self.settings.telegram_bot_token
        chat_id = to or self.settings.telegram_chat_id
        if not token:
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env when COMMS_BACKEND=telegram")
        if not chat_id:
            raise RuntimeError("Set TELEGRAM_CHAT_ID in .env when COMMS_BACKEND=telegram")

        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        for chunk in split_message(body, TELEGRAM_TEXT_LIMIT):
            post_json(
                api_url,
                {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            )
        logger.info("Telegram message sent")
        return True

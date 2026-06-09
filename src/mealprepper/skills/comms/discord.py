from __future__ import annotations

import logging

from mealprepper.config import Settings, get_settings
from mealprepper.skills.comms.base import CommsBackend
from mealprepper.skills.comms.http_utils import post_json, split_message

logger = logging.getLogger(__name__)

DISCORD_CONTENT_LIMIT = 1900


class DiscordWebhookCommsBackend(CommsBackend):
    """Post messages to a Discord channel via a webhook URL."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(self, to: str, body: str) -> bool:
        url = self.settings.discord_webhook_url
        if not url:
            raise RuntimeError("Set DISCORD_WEBHOOK_URL in .env when COMMS_BACKEND=discord")

        for chunk in split_message(body, DISCORD_CONTENT_LIMIT):
            payload: dict = {"content": chunk}
            if to:
                payload["username"] = to
            post_json(url, payload)
        logger.info("Discord webhook message sent")
        return True

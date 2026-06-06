from __future__ import annotations

import logging

import httpx

from mealprepper.config import Settings, get_settings
from mealprepper.skills.sms.base import SMSBackend

logger = logging.getLogger(__name__)


class AppleShortcutsSMSBackend(SMSBackend):
    """POST to an Apple Shortcuts webhook that forwards to Messages."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(self, to: str, body: str) -> bool:
        url = self.settings.apple_shortcuts_webhook_url
        if not url:
            raise RuntimeError("Set APPLE_SHORTCUTS_WEBHOOK_URL in .env")

        with httpx.Client(timeout=30) as client:
            response = client.post(url, json={"to": to, "message": body})
            response.raise_for_status()
        logger.info("Apple Shortcuts webhook invoked")
        return True

from __future__ import annotations

import logging

from mealprepper.config import Settings, get_settings
from mealprepper.skills.sms.base import SMSBackend

logger = logging.getLogger(__name__)


class IMessageSMSBackend(SMSBackend):
    """Stub for native iMessage integration (macOS Shortcuts / AppleScript)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(self, to: str, body: str) -> bool:
        logger.warning(
            "iMessage backend is a stub — set SMS_BACKEND=apple_shortcuts or use console. "
            "Would send to %s: %s",
            to or "family",
            body[:80],
        )
        print(f"[imsg stub] → {to or 'family'}: {body[:200]}...")
        return True

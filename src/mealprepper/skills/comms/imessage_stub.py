from __future__ import annotations

import logging

from mealprepper.config import Settings, get_settings
from mealprepper.skills.comms.base import CommsBackend

logger = logging.getLogger(__name__)


class IMessageStubCommsBackend(CommsBackend):
    """Placeholder when running on Linux without a Mac relay — use slack or console instead."""

    def send(self, to: str, body: str) -> bool:
        logger.warning(
            "iMessage stub backend — set COMMS_BACKEND=slack (Linux) or imessage with "
            "APPLE_SHORTCUTS_WEBHOOK_URL on macOS."
        )
        print(f"[imessage stub] → {to or 'family'}: {body[:200]}...")
        return True

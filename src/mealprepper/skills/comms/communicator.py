from __future__ import annotations

import logging

from mealprepper.config import Settings, get_settings
from mealprepper.skills.comms.base import CommsBackend
from mealprepper.skills.comms.console import ConsoleCommsBackend

logger = logging.getLogger(__name__)


def get_comms_backend(settings: Settings | None = None) -> CommsBackend:
    settings = settings or get_settings()
    backend = settings.comms_backend.lower()
    if backend == "twilio":
        logger.warning(
            "Twilio backend was removed — use COMMS_BACKEND=slack with SLACK_WEBHOOK_URL instead."
        )
        return ConsoleCommsBackend()
    if backend == "slack":
        from mealprepper.skills.comms.slack import SlackWebhookCommsBackend

        return SlackWebhookCommsBackend(settings)
    if backend == "discord":
        from mealprepper.skills.comms.discord import DiscordWebhookCommsBackend

        return DiscordWebhookCommsBackend(settings)
    if backend == "telegram":
        from mealprepper.skills.comms.telegram import TelegramCommsBackend

        return TelegramCommsBackend(settings)
    if backend in {"imessage", "apple_shortcuts"}:
        from mealprepper.skills.comms.imessage import AppleShortcutsCommsBackend

        return AppleShortcutsCommsBackend(settings)
    if backend == "imsg":
        from mealprepper.skills.comms.imessage_stub import IMessageStubCommsBackend

        return IMessageStubCommsBackend(settings)
    return ConsoleCommsBackend()


class CommsCommunicatorSkill:
    """Send meal plan notifications via Slack, Discord, Telegram, iMessage, or console."""

    def __init__(self, backend: CommsBackend | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.backend = backend or get_comms_backend(self.settings)

    def send(self, body: str, to: str = "") -> bool:
        return self.backend.send(to, body)

    def send_approval_request(self, summary: str, to: str = "") -> bool:
        body = (
            "*MealPrepper — Weekly plan ready for approval*\n\n"
            f"{summary}\n\n"
            "Reply `approve` in Slack (or run `mealprepper process-feedback -m APPROVE`)."
        )
        return self.backend.send(to, body)

    def send_daily_plan(self, summary: str, to: str = "") -> bool:
        body = f"*Good morning! Today's meals:*\n\n{summary}"
        return self.backend.send(to, body)

    def send_substitution_notice(self, message: str, to: str = "") -> bool:
        body = f"*MealPrepper update:*\n\n{message}"
        return self.backend.send(to, body)

    def send_feedback_prompt(self, meal_title: str, to: str = "") -> bool:
        body = f"How was *{meal_title}*? Reply: loved / liked / neutral / disliked"
        return self.backend.send(to, body)


# Backward-compatible aliases
SMSCommunicatorSkill = CommsCommunicatorSkill
get_sms_backend = get_comms_backend

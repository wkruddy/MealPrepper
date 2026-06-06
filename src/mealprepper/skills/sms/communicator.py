from __future__ import annotations

from mealprepper.config import Settings, get_settings
from mealprepper.skills.sms.base import SMSBackend
from mealprepper.skills.sms.console import ConsoleSMSBackend


def get_sms_backend(settings: Settings | None = None) -> SMSBackend:
    settings = settings or get_settings()
    backend = settings.sms_backend.lower()
    if backend == "twilio":
        from mealprepper.skills.sms.twilio import TwilioSMSBackend

        return TwilioSMSBackend(settings)
    if backend == "apple_shortcuts":
        from mealprepper.skills.sms.apple_shortcuts import AppleShortcutsSMSBackend

        return AppleShortcutsSMSBackend(settings)
    if backend == "imsg":
        from mealprepper.skills.sms.imsg import IMessageSMSBackend

        return IMessageSMSBackend(settings)
    return ConsoleSMSBackend()


class SMSCommunicatorSkill:
    """Send meal plan communications via configurable SMS backend."""

    def __init__(self, backend: SMSBackend | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.backend = backend or get_sms_backend(self.settings)

    def send(self, body: str, to: str = "") -> bool:
        return self.backend.send(to, body)

    def send_approval_request(self, summary: str, to: str = "") -> bool:
        body = f"MealPrepper — Weekly plan ready for approval:\n\n{summary}\n\nReply APPROVE or suggest changes."
        return self.backend.send(to, body)

    def send_daily_plan(self, summary: str, to: str = "") -> bool:
        body = f"Good morning! Today's meals:\n\n{summary}"
        return self.backend.send(to, body)

    def send_substitution_notice(self, message: str, to: str = "") -> bool:
        body = f"MealPrepper update:\n\n{message}"
        return self.backend.send(to, body)

    def send_feedback_prompt(self, meal_title: str, to: str = "") -> bool:
        body = f"How was '{meal_title}'? Reply: loved / liked / neutral / disliked"
        return self.backend.send(to, body)

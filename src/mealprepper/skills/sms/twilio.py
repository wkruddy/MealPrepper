from __future__ import annotations

import logging

from mealprepper.config import Settings, get_settings
from mealprepper.skills.sms.base import SMSBackend

logger = logging.getLogger(__name__)


class TwilioSMSBackend(SMSBackend):
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(self, to: str, body: str) -> bool:
        try:
            from twilio.rest import Client
        except ImportError as exc:
            raise RuntimeError("Install twilio: pip install mealprepper[twilio]") from exc

        client = Client(self.settings.twilio_account_sid, self.settings.twilio_auth_token)
        recipient = to or self.settings.twilio_to_number
        message = client.messages.create(
            body=body,
            from_=self.settings.twilio_from_number,
            to=recipient,
        )
        logger.info("Twilio message sent: %s", message.sid)
        return True

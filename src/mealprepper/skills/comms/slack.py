from __future__ import annotations

import logging

from mealprepper.config import Settings, get_settings
from mealprepper.skills.comms.base import CommsBackend
from mealprepper.skills.comms.http_utils import post_json, split_message

logger = logging.getLogger(__name__)

SLACK_TEXT_LIMIT = 3900


class SlackWebhookCommsBackend(CommsBackend):
    """Post messages to a Slack channel via an Incoming Webhook."""

    def __init__(
        self,
        settings: Settings | None = None,
        webhook_url: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.webhook_url = webhook_url or self.settings.slack_webhook_url

    def send(self, to: str, body: str) -> bool:
        url = self.webhook_url
        if not url:
            raise RuntimeError("Set SLACK_WEBHOOK_URL in .env when COMMS_BACKEND=slack")

        for chunk in split_message(body, SLACK_TEXT_LIMIT):
            payload = {"text": chunk}
            if to:
                payload["channel"] = to
            post_json(url, payload)
        logger.info("Slack webhook message sent")
        return True

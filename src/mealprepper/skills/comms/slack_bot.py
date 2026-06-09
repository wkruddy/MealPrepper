from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from mealprepper.config import Settings, get_settings
from mealprepper.skills.comms.bot_commands import (
    KNOWN_COMMANDS,
    MealPrepperBotHandler,
    BotReply,
    parse_command_text,
)
from mealprepper.skills.comms.slack_format import slack_message_payload

logger = logging.getLogger(__name__)


def require_slack_sdk():
    try:
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web import WebClient

        return SocketModeClient, SocketModeRequest, SocketModeResponse, WebClient
    except ImportError as exc:
        raise RuntimeError(
            "Slack bot requires slack-sdk. Install with: pip install 'mealprepper[slack]'"
        ) from exc


def _reply_payloads(reply: BotReply) -> list[dict]:
    if reply.payloads:
        return reply.payloads
    if reply.blocks:
        return [{"text": reply.text[:300] or "MealPrepper", "blocks": reply.blocks}]
    return [slack_message_payload(reply.text)]


class SlackBotListener:
    """Listen for Slack messages and slash commands via Socket Mode (no public URL)."""

    def __init__(
        self,
        settings: Settings | None = None,
        handler: MealPrepperBotHandler | None = None,
        on_reply: Callable[[str, BotReply], None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.handler = handler or MealPrepperBotHandler()
        self.on_reply = on_reply
        self._allowed_channel = self.settings.slack_channel_id.strip()

        SocketModeClient, _, _, WebClient = require_slack_sdk()
        bot_token = self.settings.slack_bot_token
        app_token = self.settings.slack_app_token
        if not bot_token:
            raise RuntimeError("Set SLACK_BOT_TOKEN in .env (Bot User OAuth Token, xoxb-...)")
        if not app_token:
            raise RuntimeError(
                "Set SLACK_APP_TOKEN in .env (App-Level Token with connections:write, xapp-...)"
            )

        self.web_client = WebClient(token=bot_token)
        self.socket_client = SocketModeClient(app_token=app_token, web_client=self.web_client)
        self.socket_client.socket_mode_request_listeners.append(self._on_request)

    def run(self) -> None:
        logger.info(
            "MealPrepper Slack bot listening (channel filter: %s)",
            self._allowed_channel or "any channel the bot is in",
        )
        self.socket_client.connect()
        try:
            while not self.socket_client.closed:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down Slack bot")
        finally:
            self.socket_client.close()

    def _on_request(self, client, req) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        try:
            if req.type == "events_api":
                self._ack(client, req)
                self._handle_event(req.payload)
            elif req.type == "slash_commands":
                self._handle_slash_command(client, req)
            else:
                self._ack(client, req)
        except Exception:
            logger.exception("Slack request handler failed")
            if req.type == "slash_commands":
                client.send_socket_mode_response(
                    SocketModeResponse(
                        envelope_id=req.envelope_id,
                        payload={"text": "Something went wrong. Check server logs."},
                    )
                )
            else:
                self._ack(client, req)

    @staticmethod
    def _ack(client, req) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    def _handle_slash_command(self, client, req) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        payload_data = req.payload
        channel = payload_data.get("channel_id", "")
        reply = self._dispatch(payload_data.get("text", "").strip(), payload_data.get("command", ""), channel)

        if reply.defer:
            ack_payload = slack_message_payload(reply.text)
            ack_payload["response_type"] = "in_channel"
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id, payload=ack_payload)
            )
            thread = threading.Thread(
                target=self._run_deferred,
                args=(reply.defer, channel, None),
                daemon=True,
            )
            thread.start()
            return

        response_payload = _reply_payloads(reply)[0]
        response_payload["response_type"] = "in_channel"
        client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id, payload=response_payload)
        )
        extra_payloads = _reply_payloads(reply)[1:]
        if extra_payloads:
            self._post_payloads(channel, None, extra_payloads)

    def _handle_event(self, payload: dict[str, Any]) -> None:
        event = payload.get("event", {})
        event_type = event.get("type", "")

        if event_type not in {"message", "app_mention"}:
            return
        if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_changed", "message_deleted"}:
            return

        channel = event.get("channel", "")
        if self._allowed_channel and channel != self._allowed_channel:
            return

        text = event.get("text", "").strip()
        if event_type == "message" and not self._should_handle_channel_message(text, channel):
            return
        if not text:
            return

        reply = self.handler.handle(text, channel=channel)
        if reply.defer:
            self._post_reply(channel, event.get("thread_ts") or event.get("ts"), reply)
            self._run_deferred(reply.defer, channel, event.get("thread_ts") or event.get("ts"))
            return
        self._post_reply(channel, event.get("thread_ts") or event.get("ts"), reply)

    def _should_handle_channel_message(self, text: str, channel: str) -> bool:
        if self._allowed_channel and channel != self._allowed_channel:
            return False
        if re.match(r"^<@[A-Z0-9]+>", text):
            return True
        command, _ = parse_command_text(text)
        if command in KNOWN_COMMANDS:
            return True
        lowered = text.lower()
        return any(word in lowered for word in ("loved", "liked", "disliked", "neutral", "approve", "reject"))

    def _dispatch(self, text: str, command: str, channel: str) -> BotReply:
        if self._allowed_channel and channel != self._allowed_channel:
            return BotReply("This bot is not configured for this channel.", success=False)

        if command in {"/mealprepper", "/mp"}:
            full_text = f"{command} {text}".strip()
        else:
            full_text = text or "help"
        return self.handler.handle(full_text, channel=channel)

    def _run_deferred(self, action: str, channel: str, thread_ts: str | None) -> None:
        try:
            reply = self.handler.run_deferred(action)
            self._post_reply(channel, thread_ts, reply)
        except Exception:
            logger.exception("Deferred Slack action failed: %s", action)
            self._post_reply(
                channel,
                thread_ts,
                BotReply("Something went wrong while running that command. Check server logs.", success=False),
            )

    def _post_reply(
        self,
        channel: str,
        thread_ts: str | None,
        reply: BotReply,
        *,
        use_ephemeral: bool = False,
    ) -> None:
        if self.on_reply:
            self.on_reply(channel, reply)

        if not channel:
            return

        payloads = _reply_payloads(reply)
        if reply.defer and len(payloads) == 1 and not reply.blocks and not reply.payloads:
            payloads = [slack_message_payload(reply.text)]

        self._post_payloads(channel, thread_ts, payloads, use_ephemeral=use_ephemeral)

    def _post_payloads(
        self,
        channel: str,
        thread_ts: str | None,
        payloads: list[dict],
        *,
        use_ephemeral: bool = False,
    ) -> None:
        anchor_ts = thread_ts
        for index, payload in enumerate(payloads):
            kwargs: dict[str, Any] = {
                "channel": channel,
                "text": payload.get("text", "MealPrepper"),
                "blocks": payload.get("blocks", []),
            }
            if anchor_ts and not use_ephemeral:
                kwargs["thread_ts"] = anchor_ts

            response = self.web_client.chat_postMessage(**kwargs)
            if not response.get("ok"):
                logger.error("Slack chat.postMessage failed: %s", response)
                continue
            if index == 0 and not anchor_ts:
                anchor_ts = response.get("ts")

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from mealprepper.config import Settings, get_settings
from mealprepper.services.family_resolver import FamilyResolver
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
        self._family_resolver = FamilyResolver(
            db_path=self.settings.database_path,
            settings=self.settings,
        )

        SocketModeClient, _, _, WebClient = require_slack_sdk()
        self._WebClient = WebClient
        bot_token = self.settings.slack_bot_token
        app_token = self.settings.slack_app_token
        if not bot_token:
            raise RuntimeError("Set SLACK_BOT_TOKEN in .env (Bot User OAuth Token, xoxb-...)")
        if not app_token:
            raise RuntimeError(
                "Set SLACK_APP_TOKEN in .env (App-Level Token with connections:write, xapp-...)"
            )

        self._default_bot_token = bot_token
        self._web_clients: dict[str, Any] = {}
        self.web_client = self._web_client_for_workspace("")
        self.socket_client = SocketModeClient(app_token=app_token, web_client=self.web_client)
        self.socket_client.socket_mode_request_listeners.append(self._on_request)

    def _channel_allowed(self, channel: str, workspace_id: str) -> bool:
        """Bound workspaces accept any channel; legacy SLACK_CHANNEL_ID applies otherwise."""
        workspace_id = (workspace_id or "").strip()
        if workspace_id and self._family_resolver.has_workspace_binding(workspace_id):
            return True
        if self._allowed_channel:
            return channel == self._allowed_channel
        return True

    def _resolve_bot_token(self, workspace_id: str) -> str:
        workspace_id = (workspace_id or "").strip()
        if workspace_id:
            token = self._family_resolver.bot_token_for_workspace(workspace_id)
            if token:
                return token
        return self._default_bot_token

    def _web_client_for_workspace(self, workspace_id: str):
        token = self._resolve_bot_token(workspace_id)
        client = self._web_clients.get(token)
        if client is None:
            client = self._WebClient(token=token)
            self._web_clients[token] = client
        return client

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
        command_name = payload_data.get("command", "")
        command_text = payload_data.get("text", "").strip()
        user = payload_data.get("user_id", "")
        logger.info(
            "Slash command: user=%s channel=%s cmd=%s text=%r",
            user,
            channel,
            command_name,
            command_text,
        )
        team_id = payload_data.get("team_id", "")
        reply = self._dispatch(command_text, command_name, channel, team_id, user)
        self._log_reply(reply, source="slash")

        if reply.defer:
            self._ack_slash(client, req)
            thread_ts = self.handler.get_onboarding_thread_ts(team_id, user)
            posted_ts = self._post_reply(channel, thread_ts, reply, workspace_id=team_id)
            if posted_ts and not thread_ts:
                self.handler.anchor_onboarding_thread(team_id, user, posted_ts)
            thread = threading.Thread(
                target=self._run_deferred,
                args=(reply.defer, channel, thread_ts or posted_ts, team_id, user),
                daemon=True,
            )
            thread.start()
            return

        self._ack_slash(client, req)
        thread_ts = self.handler.get_onboarding_thread_ts(team_id, user)
        posted_ts = self._post_reply(channel, thread_ts, reply, workspace_id=team_id)
        if posted_ts and not thread_ts:
            self.handler.anchor_onboarding_thread(team_id, user, posted_ts)

        extra_payloads = _reply_payloads(reply)[1:]
        if extra_payloads:
            self._post_payloads(
                channel,
                posted_ts or thread_ts,
                extra_payloads,
                workspace_id=team_id,
            )

    @staticmethod
    def _ack_slash(client, req) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        client.send_socket_mode_response(
            SocketModeResponse(
                envelope_id=req.envelope_id,
                payload={"response_type": "ephemeral", "text": " "},
            )
        )

    def _handle_event(self, payload: dict[str, Any]) -> None:
        event = payload.get("event", {})
        event_type = event.get("type", "")

        if event_type not in {"message", "app_mention"}:
            return
        if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_changed", "message_deleted"}:
            return

        channel = event.get("channel", "")
        team_id = payload.get("team_id") or event.get("team", "")
        if not self._channel_allowed(channel, team_id):
            return

        text = event.get("text", "").strip()
        user = event.get("user", "")
        if event_type == "message" and text.startswith("/"):
            # Slash commands are handled separately; echoes would duplicate replies.
            return
        if event_type == "message" and re.match(r"^<@[A-Z0-9]+>", text):
            # Slack also sends app_mention for @mentions; handling both duplicates replies
            # and can trigger cannot_reply_to_message on the second post.
            return
        if event_type == "message" and not self._should_handle_channel_message(
            text, channel, team_id, user_id=user
        ):
            return
        if not text:
            return

        logger.info(
            "Channel message: user=%s team=%s channel=%s text=%r",
            user,
            team_id,
            channel,
            text,
        )
        reply = self.handler.handle(
            text,
            channel=channel,
            workspace_id=team_id,
            slack_user_id=user,
            message_ts=str(event.get("ts", "") or ""),
            thread_ts=str(event.get("thread_ts", "") or ""),
        )
        self._log_reply(reply, source="message")
        thread_ts = self._reply_thread_ts(event, event_type, team_id, user)
        if reply.defer:
            self._post_reply(channel, thread_ts, reply, workspace_id=team_id)
            self._run_deferred(reply.defer, channel, thread_ts, team_id, user)
            return
        self._post_reply(channel, thread_ts, reply, workspace_id=team_id)

    def _should_handle_channel_message(
        self,
        text: str,
        channel: str,
        workspace_id: str = "",
        user_id: str = "",
    ) -> bool:
        if not self._channel_allowed(channel, workspace_id):
            return False
        if text.startswith("/"):
            return False
        if user_id and self.handler.should_accept_onboarding_message(workspace_id, user_id):
            return True
        if re.match(r"^<@[A-Z0-9]+>", text):
            return True
        command, _ = parse_command_text(text)
        if command in KNOWN_COMMANDS:
            return True
        lowered = text.lower()
        return any(word in lowered for word in (
            "loved", "liked", "disliked", "neutral", "approve", "reject",
            "settings", "track macros",
        ))

    def _dispatch(
        self,
        text: str,
        command: str,
        channel: str,
        workspace_id: str = "",
        slack_user_id: str = "",
    ) -> BotReply:
        if not self._channel_allowed(channel, workspace_id):
            return BotReply("This bot is not configured for this channel.", success=False)

        if command in {"/mealprepper", "/mp"}:
            full_text = f"{command} {text}".strip()
        else:
            full_text = text or "help"
        return self.handler.handle(
            full_text,
            channel=channel,
            workspace_id=workspace_id,
            slack_user_id=slack_user_id,
        )

    def _reply_thread_ts(
        self,
        event: dict[str, Any],
        event_type: str,
        workspace_id: str = "",
        user: str = "",
    ) -> str | None:
        """Pick a thread anchor; onboarding always stays in one thread."""
        if workspace_id and user:
            session_thread = self.handler.get_onboarding_thread_ts(workspace_id, user)
            if session_thread:
                return session_thread

        existing = event.get("thread_ts")
        if existing:
            return str(existing)
        if event_type == "app_mention":
            ts = event.get("ts")
            return str(ts) if ts else None
        return None

    @staticmethod
    def _log_reply(reply: BotReply, *, source: str) -> None:
        logger.info(
            "Reply (%s): success=%s defer=%s text=%r",
            source,
            reply.success,
            reply.defer,
            reply.text[:240],
        )

    def _run_deferred(
        self,
        action: str,
        channel: str,
        thread_ts: str | None,
        workspace_id: str = "",
        slack_user_id: str = "",
    ) -> None:
        logger.info(
            "Deferred action started: %s channel=%s workspace=%s user=%s",
            action,
            channel,
            workspace_id or "(default)",
            slack_user_id or "(none)",
        )
        try:
            reply = self.handler.run_deferred(
                action,
                workspace_id=workspace_id,
                channel=channel,
                slack_user_id=slack_user_id,
            )
            self._log_reply(reply, source=f"deferred:{action}")
            self._post_reply(channel, thread_ts, reply, workspace_id=workspace_id)
        except Exception:
            logger.exception("Deferred Slack action failed: %s", action)
            self._post_reply(
                channel,
                thread_ts,
                BotReply("Something went wrong while running that command. Check server logs.", success=False),
                workspace_id=workspace_id,
            )

    def _post_reply(
        self,
        channel: str,
        thread_ts: str | None,
        reply: BotReply,
        *,
        use_ephemeral: bool = False,
        workspace_id: str = "",
    ) -> str | None:
        if self.on_reply:
            self.on_reply(channel, reply)

        if not channel:
            return None

        payloads = _reply_payloads(reply)
        if reply.defer and len(payloads) == 1 and not reply.blocks and not reply.payloads:
            payloads = [slack_message_payload(reply.text)]

        return self._post_payloads(
            channel,
            thread_ts,
            payloads,
            use_ephemeral=use_ephemeral,
            workspace_id=workspace_id,
        )

    def _post_payloads(
        self,
        channel: str,
        thread_ts: str | None,
        payloads: list[dict],
        *,
        use_ephemeral: bool = False,
        workspace_id: str = "",
    ) -> str | None:
        web_client = self._web_client_for_workspace(workspace_id)
        anchor_ts = thread_ts
        first_ts: str | None = None
        for index, payload in enumerate(payloads):
            kwargs: dict[str, Any] = {
                "channel": channel,
                "text": payload.get("text", "MealPrepper"),
                "blocks": payload.get("blocks", []),
            }
            if anchor_ts and not use_ephemeral:
                kwargs["thread_ts"] = anchor_ts

            response = self._post_message(web_client, kwargs)
            if not response:
                continue
            message_ts = response.get("ts")
            if index == 0:
                first_ts = message_ts
            if index == 0 and not anchor_ts:
                anchor_ts = message_ts
        return first_ts or anchor_ts

    def _post_message(self, web_client, kwargs: dict[str, Any]):
        try:
            response = web_client.chat_postMessage(**kwargs)
        except Exception as exc:
            if not self._is_cannot_reply_error(exc):
                logger.exception("Slack chat.postMessage failed")
                return None
            thread_ts = kwargs.pop("thread_ts", None)
            if not thread_ts:
                logger.error("Slack chat.postMessage failed: %s", exc)
                return None
            logger.warning(
                "Slack rejected threaded reply (cannot_reply_to_message); retrying in channel"
            )
            try:
                response = web_client.chat_postMessage(**kwargs)
            except Exception:
                logger.exception("Slack chat.postMessage retry failed")
                return None

        if not response.get("ok"):
            logger.error("Slack chat.postMessage failed: %s", response)
            return None
        return response

    @staticmethod
    def _is_cannot_reply_error(exc: Exception) -> bool:
        from slack_sdk.errors import SlackApiError

        if isinstance(exc, SlackApiError):
            return exc.response.get("error") == "cannot_reply_to_message"
        return "cannot_reply_to_message" in str(exc)

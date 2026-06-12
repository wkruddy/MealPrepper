from unittest.mock import MagicMock, patch

from mealprepper.skills.comms.slack_bot import SlackBotListener


def test_channel_allowed_for_bound_workspace():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener._allowed_channel = "C_DEV_ONLY"
    listener._family_resolver = MagicMock()
    listener._family_resolver.has_workspace_binding.return_value = True

    assert listener._channel_allowed("C_OTHER", "T_FRACTAL") is True
    listener._family_resolver.has_workspace_binding.assert_called_with("T_FRACTAL")


def test_channel_allowed_legacy_filter():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener._allowed_channel = "C_DEV_ONLY"
    listener._family_resolver = MagicMock()
    listener._family_resolver.has_workspace_binding.return_value = False

    assert listener._channel_allowed("C_DEV_ONLY", "T_DEV") is True
    assert listener._channel_allowed("C_OTHER", "T_DEV") is False


def test_reply_thread_ts_only_threads_existing_or_app_mention():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener.handler = MagicMock()
    listener.handler.get_onboarding_thread_ts.return_value = None

    assert listener._reply_thread_ts({"thread_ts": "111.222"}, "message") == "111.222"
    assert listener._reply_thread_ts({"ts": "333.444"}, "app_mention") == "333.444"
    assert listener._reply_thread_ts({"ts": "333.444"}, "message") is None


def test_reply_thread_ts_uses_onboarding_session():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener.handler = MagicMock()
    listener.handler.get_onboarding_thread_ts.return_value = "555.666"

    thread_ts = listener._reply_thread_ts(
        {"ts": "777.888"},
        "message",
        workspace_id="T1",
        user="U1",
    )
    assert thread_ts == "555.666"
    listener.handler.get_onboarding_thread_ts.assert_called_once_with("T1", "U1")


def test_should_handle_onboarding_follow_up():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener._allowed_channel = ""
    listener._family_resolver = MagicMock()
    listener._family_resolver.has_workspace_binding.return_value = True
    listener.handler = MagicMock()
    listener.handler.should_accept_onboarding_message.return_value = True

    assert listener._should_handle_channel_message(
        "Thoms house",
        "C1",
        "T1",
        user_id="U1",
    ) is True


def test_should_ignore_slash_command_message_echo():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener._allowed_channel = ""
    listener._family_resolver = MagicMock()
    listener._family_resolver.has_workspace_binding.return_value = True
    listener.handler = MagicMock()

    assert listener._should_handle_channel_message("/mp start", "C1", "T1", user_id="U1") is False


def test_ack_slash_uses_ephemeral():
    listener = SlackBotListener.__new__(SlackBotListener)
    client = MagicMock()
    req = MagicMock(envelope_id="env-1")
    listener._ack_slash(client, req)
    payload = client.send_socket_mode_response.call_args[0][0].payload
    assert payload["response_type"] == "ephemeral"


def test_post_message_retries_without_thread_on_cannot_reply():
    from slack_sdk.errors import SlackApiError

    listener = SlackBotListener.__new__(SlackBotListener)
    web_client = MagicMock()
    web_client.chat_postMessage.side_effect = [
        SlackApiError("failed", {"ok": False, "error": "cannot_reply_to_message"}),
        {"ok": True, "ts": "999.000"},
    ]
    kwargs = {"channel": "C1", "text": "hello", "thread_ts": "111.000"}
    response = listener._post_message(web_client, kwargs)
    assert response["ok"] is True
    assert web_client.chat_postMessage.call_count == 2
    assert "thread_ts" not in web_client.chat_postMessage.call_args_list[1].kwargs


def test_run_blocks_until_closed():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener._allowed_channel = ""
    listener.socket_client = MagicMock()
    listener.socket_client.closed = False

    def close_on_second_sleep(_seconds: float) -> None:
        if listener.socket_client.closed is False:
            listener.socket_client.closed = True

    with patch("mealprepper.skills.comms.slack_bot.time.sleep", side_effect=close_on_second_sleep):
        listener.run()

    listener.socket_client.connect.assert_called_once()
    listener.socket_client.close.assert_called_once()

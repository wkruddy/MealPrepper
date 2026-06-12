from unittest.mock import MagicMock, patch

from mealprepper.skills.comms.slack_bot import SlackBotListener


def test_web_client_uses_workspace_bot_token():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener._default_bot_token = "xoxb-default"
    listener._web_clients = {}
    listener._WebClient = MagicMock(side_effect=lambda token: MagicMock(token=token))
    listener._family_resolver = MagicMock()
    listener._family_resolver.bot_token_for_workspace.return_value = "xoxb-friend"

    client = listener._web_client_for_workspace("T_FRIEND")
    assert client.token == "xoxb-friend"
    listener._family_resolver.bot_token_for_workspace.assert_called_with("T_FRIEND")

    default_client = listener._web_client_for_workspace("")
    assert default_client.token == "xoxb-default"


def test_web_client_falls_back_to_env_token():
    listener = SlackBotListener.__new__(SlackBotListener)
    listener._default_bot_token = "xoxb-default"
    listener._web_clients = {}
    listener._WebClient = MagicMock(side_effect=lambda token: MagicMock(token=token))
    listener._family_resolver = MagicMock()
    listener._family_resolver.bot_token_for_workspace.return_value = ""

    client = listener._web_client_for_workspace("T_UNKNOWN")
    assert client.token == "xoxb-default"

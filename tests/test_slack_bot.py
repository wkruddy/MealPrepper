from unittest.mock import MagicMock, patch

from mealprepper.skills.comms.slack_bot import SlackBotListener


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

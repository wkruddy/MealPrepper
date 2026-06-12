from unittest.mock import MagicMock, patch

import httpx

from mealprepper.skills.comms.slack_oauth import (
    build_authorize_url,
    build_redirect_uri,
    exchange_oauth_code,
    format_oauth_result,
    slack_redirect_insecure_message,
)


def test_build_redirect_uri_localhost():
    uri = build_redirect_uri("127.0.0.1", 8787)
    assert uri == "http://127.0.0.1:8787/slack/oauth/callback"


def test_build_redirect_uri_ngrok():
    uri = build_redirect_uri("abc123.ngrok-free.app", 8787)
    assert uri == "https://abc123.ngrok-free.app/slack/oauth/callback"


def test_build_authorize_url_includes_redirect_uri():
    url = build_authorize_url(
        client_id="324010600710.11314433222885",
        redirect_uri="http://127.0.0.1:8787/slack/oauth/callback",
    )
    assert "client_id=324010600710.11314433222885" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8787%2Fslack%2Foauth%2Fcallback" in url
    assert "scope=chat%3Awrite" in url
    assert url.startswith("https://slack.com/oauth/v2/authorize?")


def test_exchange_oauth_code_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "ok": True,
        "access_token": "xoxb-test",
        "team": {"id": "T_FRACTAL", "name": "Fractal Productions"},
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response

    with patch("mealprepper.skills.comms.slack_oauth.httpx.Client", return_value=mock_client):
        payload = exchange_oauth_code(
            client_id="cid",
            client_secret="secret",
            code="auth-code",
            redirect_uri="http://127.0.0.1:8787/slack/oauth/callback",
        )

    assert payload["access_token"] == "xoxb-test"
    mock_client.post.assert_called_once_with(
        "https://slack.com/api/oauth.v2.access",
        data={
            "client_id": "cid",
            "client_secret": "secret",
            "code": "auth-code",
            "redirect_uri": "http://127.0.0.1:8787/slack/oauth/callback",
        },
    )


def test_exchange_oauth_code_slack_error():
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": False, "error": "bad_redirect_uri"}
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response

    with patch("mealprepper.skills.comms.slack_oauth.httpx.Client", return_value=mock_client):
        try:
            exchange_oauth_code(
                client_id="cid",
                client_secret="secret",
                code="auth-code",
                redirect_uri="http://example.com/callback",
            )
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "bad_redirect_uri" in str(exc)

    assert raised


def test_slack_redirect_insecure_message():
    assert slack_redirect_insecure_message("http://127.0.0.1:8787/slack/oauth/callback") is not None
    assert slack_redirect_insecure_message("https://abc.ngrok-free.app/slack/oauth/callback") is None


def test_format_oauth_result():
    text = format_oauth_result(
        {
            "access_token": "xoxb-abc",
            "team": {"id": "T123", "name": "Fractal"},
            "incoming_webhook": {
                "url": "https://hooks.slack.com/services/X",
                "channel": "#food",
                "channel_id": "C999",
            },
        }
    )
    assert "Fractal (T123)" in text
    assert "xoxb-abc" in text
    assert "hooks.slack.com" in text

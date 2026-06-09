import mealprepper.skills.comms.slack as slack_module
from mealprepper.config import Settings
from mealprepper.skills.comms.communicator import CommsCommunicatorSkill, get_comms_backend
from mealprepper.skills.comms.http_utils import split_message
from mealprepper.skills.comms.slack import SlackWebhookCommsBackend


def test_split_message_respects_limit():
    body = "line\n" * 50
    chunks = split_message(body, 20)
    assert len(chunks) > 1
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_get_comms_backend_slack():
    settings = Settings(COMMS_BACKEND="slack")
    backend = get_comms_backend(settings)
    assert isinstance(backend, SlackWebhookCommsBackend)


def test_get_comms_backend_twilio_falls_back_to_console():
    settings = Settings(COMMS_BACKEND="twilio")
    backend = get_comms_backend(settings)
    assert backend.__class__.__name__ == "ConsoleCommsBackend"


def test_slack_webhook_send(monkeypatch):
    calls: list[dict] = []

    def fake_post(url, payload, *, timeout=30.0):
        calls.append({"url": url, "payload": payload})

    monkeypatch.setattr(slack_module, "post_json", fake_post)
    settings = Settings(COMMS_BACKEND="slack", SLACK_WEBHOOK_URL="https://hooks.slack.com/test")
    backend = SlackWebhookCommsBackend(settings)
    assert backend.send("", "Hello from MealPrepper")
    assert calls[0]["url"] == "https://hooks.slack.com/test"
    assert calls[0]["payload"]["text"] == "Hello from MealPrepper"


def test_comms_communicator_formats_approval():
    sent: list[str] = []

    class FakeBackend:
        def send(self, to: str, body: str) -> bool:
            sent.append(body)
            return True

    skill = CommsCommunicatorSkill(backend=FakeBackend())
    skill.send_approval_request("Mon: tacos\nTue: pasta")
    assert "Weekly plan ready for approval" in sent[0]
    assert "Mon: tacos" in sent[0]
    assert "APPROVE" in sent[0]

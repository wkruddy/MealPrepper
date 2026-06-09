"""Backward-compatible re-exports — prefer mealprepper.skills.comms."""

from mealprepper.skills.comms import (
    CommsBackend,
    CommsCommunicatorSkill,
    ConsoleCommsBackend,
    SMSCommunicatorSkill,
    get_comms_backend,
    get_sms_backend,
)
from mealprepper.skills.comms.imessage import AppleShortcutsCommsBackend
from mealprepper.skills.comms.slack import SlackWebhookCommsBackend

SMSBackend = CommsBackend
ConsoleSMSBackend = ConsoleCommsBackend
AppleShortcutsSMSBackend = AppleShortcutsCommsBackend
SlackWebhookSMSBackend = SlackWebhookCommsBackend

__all__ = [
    "CommsBackend",
    "CommsCommunicatorSkill",
    "ConsoleCommsBackend",
    "SMSBackend",
    "ConsoleSMSBackend",
    "AppleShortcutsSMSBackend",
    "SlackWebhookSMSBackend",
    "SMSCommunicatorSkill",
    "get_comms_backend",
    "get_sms_backend",
]

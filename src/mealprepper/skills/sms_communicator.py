"""Backward-compatible re-exports — prefer mealprepper.skills.sms."""

from mealprepper.skills.sms import (
    ConsoleSMSBackend,
    SMSBackend,
    SMSCommunicatorSkill,
    get_sms_backend,
)
from mealprepper.skills.sms.apple_shortcuts import AppleShortcutsSMSBackend
from mealprepper.skills.sms.twilio import TwilioSMSBackend

__all__ = [
    "SMSBackend",
    "ConsoleSMSBackend",
    "TwilioSMSBackend",
    "AppleShortcutsSMSBackend",
    "get_sms_backend",
    "SMSCommunicatorSkill",
]

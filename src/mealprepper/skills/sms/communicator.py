"""Backward-compatible re-exports — prefer mealprepper.skills.comms."""

from mealprepper.skills.comms.communicator import (
    CommsCommunicatorSkill,
    SMSCommunicatorSkill,
    get_comms_backend,
    get_sms_backend,
)

__all__ = [
    "CommsCommunicatorSkill",
    "SMSCommunicatorSkill",
    "get_comms_backend",
    "get_sms_backend",
]

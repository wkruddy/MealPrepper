"""Backward-compatible re-exports — prefer mealprepper.skills.comms.imessage_stub."""

from mealprepper.skills.comms.imessage_stub import IMessageStubCommsBackend

IMessageSMSBackend = IMessageStubCommsBackend

__all__ = ["IMessageStubCommsBackend", "IMessageSMSBackend"]

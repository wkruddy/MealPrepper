"""Backward-compatible re-exports — prefer mealprepper.skills.comms.base."""

from mealprepper.skills.comms.base import CommsBackend

SMSBackend = CommsBackend

__all__ = ["CommsBackend", "SMSBackend"]

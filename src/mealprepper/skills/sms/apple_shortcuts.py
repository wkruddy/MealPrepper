"""Backward-compatible re-exports — prefer mealprepper.skills.comms.imessage."""

from mealprepper.skills.comms.imessage import AppleShortcutsCommsBackend

AppleShortcutsSMSBackend = AppleShortcutsCommsBackend

__all__ = ["AppleShortcutsCommsBackend", "AppleShortcutsSMSBackend"]

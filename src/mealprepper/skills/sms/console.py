"""Backward-compatible re-exports — prefer mealprepper.skills.comms.console."""

from mealprepper.skills.comms.console import ConsoleCommsBackend

ConsoleSMSBackend = ConsoleCommsBackend

__all__ = ["ConsoleCommsBackend", "ConsoleSMSBackend"]

from __future__ import annotations

from mealprepper.skills.comms.base import CommsBackend


class ConsoleCommsBackend(CommsBackend):
    """Print notifications to stdout (dev / VM without external integrations)."""

    def send(self, to: str, body: str) -> bool:
        recipient = to or "family"
        print(f"MealPrepper ({recipient})")
        print("-" * 40)
        print(body)
        print("-" * 40)
        return True

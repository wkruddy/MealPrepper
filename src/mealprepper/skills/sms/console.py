from __future__ import annotations

from mealprepper.skills.sms.base import SMSBackend


class ConsoleSMSBackend(SMSBackend):
    """Development backend — prints messages to stdout."""

    def send(self, to: str, body: str) -> bool:
        print("\n" + "=" * 60)
        print(f"SMS (console mock) → {to or 'family'}")
        print("-" * 60)
        print(body)
        print("=" * 60 + "\n")
        return True

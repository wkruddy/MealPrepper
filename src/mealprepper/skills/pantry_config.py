from __future__ import annotations

import re
from dataclasses import dataclass, field

from mealprepper.config import Settings, get_settings


@dataclass
class PantryConfig:
    on_hand: set[str] = field(default_factory=set)
    weekly_staples: set[str] = field(default_factory=set)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> PantryConfig:
        settings = settings or get_settings()
        raw = settings.load_yaml("pantry.yaml")
        on_hand: set[str] = set()
        for group in raw.get("on_hand", {}).values():
            if isinstance(group, list):
                on_hand.update(_normalize_name(item) for item in group)
        weekly = {_normalize_name(item) for item in raw.get("weekly_staples", [])}
        return cls(on_hand=on_hand, weekly_staples=weekly)

    def matches_on_hand(self, name: str) -> bool:
        key = _normalize_name(name)
        if key in self.on_hand:
            return True
        return any(key in stocked or stocked in key for stocked in self.on_hand)

    def matches_weekly_staple(self, name: str) -> bool:
        key = _normalize_name(name)
        if key in self.weekly_staples:
            return True
        return any(key in staple or staple in key for staple in self.weekly_staples)


def _normalize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

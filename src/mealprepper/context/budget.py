from __future__ import annotations

from enum import Enum
from typing import Any

from mealprepper.config import Settings, get_settings


class CallType(str, Enum):
    MEAL_FINDER = "meal_finder"
    RECIPE_EXPAND = "recipe_expand"
    RECIPE_IMPORT = "recipe_import"
    WEEK_ORGANIZER = "week_organizer"
    GROCERY = "grocery"
    COMMS = "comms"
    PREFERENCE = "preference"
    DEFAULT = "default"


# Rough chars-per-token estimate for local models (conservative).
CHARS_PER_TOKEN = 4


class ContextBudget:
    """Configurable max context size per LLM call type."""

    def __init__(
        self,
        *,
        max_chars: int = 12000,
        per_call: dict[str, int] | None = None,
    ) -> None:
        self.max_chars = max_chars
        self.per_call = per_call or {}

    def limit_for(self, call_type: CallType | str) -> int:
        key = call_type.value if isinstance(call_type, CallType) else str(call_type)
        return self.per_call.get(key, self.max_chars)

    def max_tokens_for(self, call_type: CallType | str) -> int:
        return self.limit_for(call_type) // CHARS_PER_TOKEN

    def truncate(self, text: str, call_type: CallType | str, *, label: str = "") -> str:
        limit = self.limit_for(call_type)
        if len(text) <= limit:
            return text
        suffix = f"\n...[truncated to {limit} chars"
        if label:
            suffix += f" for {label}"
        suffix += "]"
        keep = max(0, limit - len(suffix))
        return text[:keep] + suffix

    def fits(self, text: str, call_type: CallType | str) -> bool:
        return len(text) <= self.limit_for(call_type)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None, settings: Settings | None = None) -> ContextBudget:
        settings = settings or get_settings()
        cfg = config if config is not None else settings.merged_config()
        ctx = cfg.get("context", {})
        budgets = ctx.get("budgets", {})
        max_chars = int(ctx.get("max_context_chars", settings.max_context_chars))
        per_call = {k: int(v) for k, v in budgets.items()}
        return cls(max_chars=max_chars, per_call=per_call)


def load_context_budget(settings: Settings | None = None) -> ContextBudget:
    settings = settings or get_settings()
    return ContextBudget.from_config(settings.merged_config(), settings)

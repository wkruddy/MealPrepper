from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class FeedbackRating(str, Enum):
    LOVED = "loved"
    LIKED = "liked"
    NEUTRAL = "neutral"
    DISLIKED = "disliked"
    REJECT = "reject"


class MealFeedback(BaseModel):
    id: str | None = None
    meal_title: str
    meal_block: str = ""
    day: str = ""
    rating: FeedbackRating
    comment: str = ""
    member_id: str | None = None
    created_at: datetime | None = None
    applied_to_preferences: bool = False


class PreferenceEntry(BaseModel):
    id: str | None = None
    key: str
    value: str
    source: str = "feedback"
    score: float = 0.0
    updated_at: datetime | None = None


class PreferenceProfile(BaseModel):
    liked_meals: list[str] = Field(default_factory=list)
    disliked_meals: list[str] = Field(default_factory=list)
    liked_ingredients: list[str] = Field(default_factory=list)
    disliked_ingredients: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    notes: str = ""

    def to_prompt_context(self) -> str:
        lines = []
        if self.liked_meals:
            lines.append(f"Liked meals: {', '.join(self.liked_meals[-15:])}")
        if self.disliked_meals:
            lines.append(f"Disliked meals: {', '.join(self.disliked_meals[-15:])}")
        if self.liked_ingredients:
            lines.append(f"Liked ingredients: {', '.join(self.liked_ingredients[-15:])}")
        if self.disliked_ingredients:
            lines.append(f"Disliked ingredients: {', '.join(self.disliked_ingredients[-15:])}")
        if self.constraints:
            lines.append(f"Hard constraints: {', '.join(self.constraints)}")
        if self.notes:
            notes = self.notes[-500:] if len(self.notes) > 500 else self.notes
            lines.append(f"Notes: {notes}")
        return "\n".join(lines) if lines else "No preference history yet."

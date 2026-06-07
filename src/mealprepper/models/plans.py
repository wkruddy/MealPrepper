from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from mealprepper.models.meals import Ingredient, PlannedMeal


class PlanStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACTIVE = "active"
    COMPLETED = "completed"


class WeeklyPlan(BaseModel):
    id: str | None = None
    week_start: date
    week_end: date
    status: PlanStatus = PlanStatus.DRAFT
    meals: list[PlannedMeal] = Field(default_factory=list)
    synergy_notes: str = ""
    synergy_suggestions: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    approved_at: datetime | None = None
    playbook_markdown: str = ""

    def ingredient_names(self) -> list[str]:
        names: list[str] = []
        for meal in self.meals:
            for ing in meal.recipe.ingredients:
                names.append(ing.name.lower().strip())
        return names

    def meals_for_day(self, day: str) -> list[PlannedMeal]:
        return [m for m in self.meals if m.day.lower() == day.lower()]


class DailyPlanSummary(BaseModel):
    plan_date: date
    day_name: str
    meals: list[PlannedMeal] = Field(default_factory=list)
    prep_notes: str = ""
    infant_blw_tips: str = ""

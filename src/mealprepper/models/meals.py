from __future__ import annotations

from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    name: str
    quantity: str = ""
    unit: str = ""
    notes: str = ""
    category: str = "other"


class RecipeStep(BaseModel):
    order: int
    instruction: str
    duration_minutes: int | None = None


class MealRecipe(BaseModel):
    title: str
    description: str = ""
    prep_minutes: int = 0
    cook_minutes: int = 0
    servings: int = 4
    ingredients: list[Ingredient] = Field(default_factory=list)
    steps: list[RecipeStep] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    infant_guidance: str = ""
    toddler_modifications: str = ""

    @property
    def total_minutes(self) -> int:
        return self.prep_minutes + self.cook_minutes


class PlannedMeal(BaseModel):
    meal_block: str
    day: str
    recipe: MealRecipe
    member_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class MealCandidate(BaseModel):
    """A meal suggestion before full recipe detail is attached."""

    title: str
    meal_block: str
    rationale: str = ""
    key_ingredients: list[str] = Field(default_factory=list)
    prep_minutes: int = 0

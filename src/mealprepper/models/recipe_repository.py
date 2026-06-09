from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from mealprepper.models.meals import MealRecipe


class SavedRecipe(BaseModel):
    """A family-curated recipe or meal idea stored for planning."""

    id: str | None = None
    title: str
    source_type: str = "text"  # text | url | file | trello
    source_url: str = ""
    source_label: str = ""
    content_hash: str = ""
    raw_text: str = ""
    recipe: MealRecipe | None = None
    key_ingredients: list[str] = Field(default_factory=list)
    meal_blocks: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    favorite: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def has_full_recipe(self) -> bool:
        return self.recipe is not None and bool(self.recipe.ingredients or self.recipe.steps)

    def searchable_ingredients(self) -> str:
        if self.recipe and self.recipe.ingredients:
            return ", ".join(i.name for i in self.recipe.ingredients)
        return ", ".join(self.key_ingredients)

    def to_meal_recipe(self, fallback_title: str | None = None) -> MealRecipe:
        if self.recipe:
            recipe = self.recipe.model_copy(deep=True)
            if fallback_title:
                recipe.title = fallback_title
            return recipe
        title = fallback_title or self.title
        from mealprepper.models.meals import Ingredient

        return MealRecipe(
            title=title,
            description=self.notes,
            ingredients=[Ingredient(name=name, quantity="1", unit="portion") for name in self.key_ingredients],
            tags=list(self.tags),
        )

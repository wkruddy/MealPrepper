from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from mealprepper.models.meals import Ingredient


class GroceryCategory(str, Enum):
    PRODUCE = "produce"
    DAIRY = "dairy"
    MEAT = "meat"
    PANTRY = "pantry"
    FROZEN = "frozen"
    SPICES = "spices"
    OTHER = "other"


class GroceryItem(BaseModel):
    name: str
    quantity: str = ""
    unit: str = ""
    category: GroceryCategory = GroceryCategory.OTHER
    used_in_meals: list[str] = Field(default_factory=list)
    checked: bool = False
    notes: str = ""


class GroceryList(BaseModel):
    id: str | None = None
    weekly_plan_id: str | None = None
    week_label: str = ""
    items: list[GroceryItem] = Field(default_factory=list)
    synergy_notes: str = ""
    created_at: datetime | None = None
    ready_for_shopping: bool = False

    def unchecked_items(self) -> list[GroceryItem]:
        return [i for i in self.items if not i.checked]

    @classmethod
    def from_ingredients(
        cls,
        ingredients: list[Ingredient],
        week_label: str = "",
        meal_titles: dict[str, str] | None = None,
    ) -> GroceryList:
        """Build a basic list from raw ingredients (deduped by name)."""
        meal_titles = meal_titles or {}
        seen: dict[str, GroceryItem] = {}
        for ing in ingredients:
            key = ing.name.lower().strip()
            if key in seen:
                existing = seen[key]
                if ing.quantity and existing.quantity:
                    existing.quantity = f"{existing.quantity}, {ing.quantity}"
                continue
            category = GroceryCategory.OTHER
            try:
                category = GroceryCategory(ing.category)
            except ValueError:
                pass
            seen[key] = GroceryItem(
                name=ing.name,
                quantity=ing.quantity,
                unit=ing.unit,
                category=category,
                notes=ing.notes,
            )
        return cls(week_label=week_label, items=list(seen.values()))

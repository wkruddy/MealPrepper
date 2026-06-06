from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError
from mealprepper.models.grocery import GroceryCategory, GroceryItem, GroceryList
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.ingredient_synergy import IngredientSynergySkill

logger = logging.getLogger(__name__)


class GroceryBuildResult(BaseModel):
    items: list[GroceryItem] = Field(default_factory=list)
    synergy_notes: str = ""
    shopping_tips: list[str] = Field(default_factory=list)


class GroceryBuilderSkill:
    """Build a consolidated grocery list from the weekly plan."""

    SYSTEM = """You build efficient grocery lists for a family of 4 (2 adults, toddler, infant).
Consolidate duplicate ingredients, assign store categories (produce, dairy, meat, pantry, frozen, spices, other),
and note which meals use each item. Quantities should be practical for one week."""

    def __init__(
        self,
        llm: OllamaClient | None = None,
        synergy: IngredientSynergySkill | None = None,
    ) -> None:
        self.llm = llm or OllamaClient()
        self.synergy = synergy or IngredientSynergySkill()

    def build(self, plan: WeeklyPlan) -> GroceryList:
        ingredients = self.synergy.consolidate_ingredients(plan.meals)
        week_label = f"{plan.week_start.isoformat()} — {plan.week_end.isoformat()}"

        try:
            meal_map = {m.recipe.title: f"{m.day}/{m.meal_block}" for m in plan.meals}
            prompt = f"""Build a grocery list JSON object with fields:
items (array of name, quantity, unit, category, used_in_meals, notes),
synergy_notes, shopping_tips.

Week: {week_label}
Synergy context: {plan.synergy_notes}

Raw ingredients:
{self._format_ingredients(ingredients)}

Meals using ingredients:
{chr(10).join(f'- {k}: {v}' for k, v in list(meal_map.items())[:15])}"""

            result = self.llm.chat_json(
                [
                    {"role": "system", "content": self.SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                GroceryBuildResult,
            )
            items = result.items
            synergy_notes = result.synergy_notes
        except (OllamaUnavailableError, ValueError) as exc:
            logger.warning("Grocery LLM failed, using consolidation: %s", exc)
            base = GroceryList.from_ingredients(ingredients, week_label=week_label)
            items = base.items
            synergy_notes = plan.synergy_notes or "Consolidated from weekly plan ingredients."

        for item in items:
            if isinstance(item.category, str):
                try:
                    item.category = GroceryCategory(item.category)
                except ValueError:
                    item.category = GroceryCategory.OTHER

        return GroceryList(
            weekly_plan_id=plan.id,
            week_label=week_label,
            items=items,
            synergy_notes=synergy_notes,
            ready_for_shopping=True,
        )

    def render_text(self, grocery: GroceryList) -> str:
        lines = [f"# Grocery List — {grocery.week_label}", ""]
        if grocery.synergy_notes:
            lines.append(f"_{grocery.synergy_notes}_\n")

        by_cat: dict[str, list[GroceryItem]] = {}
        for item in grocery.items:
            cat = item.category.value if hasattr(item.category, "value") else str(item.category)
            by_cat.setdefault(cat, []).append(item)

        for cat in sorted(by_cat.keys()):
            lines.append(f"## {cat.title()}")
            for item in by_cat[cat]:
                qty = f" — {item.quantity} {item.unit}".strip()
                lines.append(f"- [ ] {item.name}{qty}")
                if item.used_in_meals:
                    lines.append(f"  - Used in: {', '.join(item.used_in_meals[:3])}")
            lines.append("")
        return "\n".join(lines)

    def _format_ingredients(self, ingredients) -> str:
        return "\n".join(
            f"- {i.name}: {i.quantity} {i.unit} ({i.category})" for i in ingredients
        )

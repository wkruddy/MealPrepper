from __future__ import annotations

import logging
from collections import defaultdict

from pydantic import BaseModel, Field, field_validator

from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError
from mealprepper.models.grocery import GroceryList
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.grocery_normalizer import GroceryNormalizer
from mealprepper.skills.ingredient_synergy import IngredientSynergySkill

logger = logging.getLogger(__name__)


class GroceryNotesResult(BaseModel):
    synergy_notes: str = ""
    shopping_tips: list[str] = Field(default_factory=list)

    @field_validator("shopping_tips", mode="before")
    @classmethod
    def normalize_shopping_tips(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            tips = []
            for line in value.replace(";", "\n").splitlines():
                cleaned = line.strip().lstrip("0123456789.) ")
                if cleaned:
                    tips.append(cleaned)
            return tips
        return value


class GroceryBuilderSkill:
    """Build a consolidated, human-friendly grocery list from the weekly plan."""

    SYSTEM = """You help a family shop for one week of meals.
Review the consolidated ingredient list and return brief synergy_notes (ingredient overlap, waste reduction)
and shopping_tips (array of short strings). Do NOT return the item list — quantities are handled separately."""

    def __init__(
        self,
        llm: OllamaClient | None = None,
        synergy: IngredientSynergySkill | None = None,
        normalizer: GroceryNormalizer | None = None,
    ) -> None:
        self.llm = llm or OllamaClient()
        self.synergy = synergy or IngredientSynergySkill()
        self.normalizer = normalizer or GroceryNormalizer()

    def build(self, plan: WeeklyPlan) -> GroceryList:
        ingredients = self.synergy.consolidate_ingredients(plan.meals)
        week_label = f"{plan.week_start.isoformat()} — {plan.week_end.isoformat()}"
        synergy_notes = plan.synergy_notes or ""

        try:
            prompt = f"""Review this week's ingredients and suggest shopping synergy notes.

Week: {week_label}
Existing synergy notes: {synergy_notes or 'none'}

Consolidated ingredients:
{self._format_ingredients(ingredients)}

Return JSON with synergy_notes (string) and shopping_tips (array of strings)."""

            result = self.llm.chat_json(
                [
                    {"role": "system", "content": self.SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                GroceryNotesResult,
            )
            if result.synergy_notes:
                synergy_notes = result.synergy_notes
            if result.shopping_tips:
                tips = " ".join(result.shopping_tips)
                synergy_notes = f"{synergy_notes}\n\nShopping tips: {tips}".strip()
        except (OllamaUnavailableError, ValueError) as exc:
            logger.warning("Grocery notes LLM failed, using plan synergy only: %s", exc)

        grocery = self.normalizer.build_shopping_list(
            ingredients,
            week_label,
            weekly_plan_id=plan.id,
            synergy_notes=synergy_notes,
        )
        logger.info(
            "Grocery list: %d to buy, %d staples, %d pantry assumed",
            len(grocery.must_buy),
            len(grocery.weekly_staples),
            len(grocery.pantry_assumed),
        )
        return grocery

    def render_text(self, grocery: GroceryList) -> str:
        lines = [f"# Grocery List — {grocery.week_label}", ""]
        if grocery.synergy_notes:
            lines.append(f"_{grocery.synergy_notes}_\n")

        must_buy = grocery.must_buy or [i for i in grocery.items if i.section == "must_buy"]
        weekly_staples = grocery.weekly_staples or [
            i for i in grocery.items if i.section == "weekly_staple"
        ]

        if must_buy:
            lines.append("## Shop for recipes")
            lines.append("_Unique or recipe-specific items to pick up._\n")
            lines.extend(self._render_section(must_buy))
            lines.append("")

        if weekly_staples:
            lines.append("## Weekly staples")
            lines.append("_Buy if you're running low — used across multiple meals._\n")
            lines.extend(self._render_section(weekly_staples))
            lines.append("")

        if grocery.pantry_assumed:
            lines.append("## Already in pantry")
            lines.append("_Not on the shopping list — we assumed you have these:_\n")
            pantry = ", ".join(grocery.pantry_assumed)
            lines.append(f"{pantry}\n")

        return "\n".join(lines).strip() + "\n"

    def _render_section(self, items: list) -> list[str]:
        by_cat: dict[str, list] = defaultdict(list)
        for item in items:
            cat = item.category.value if hasattr(item.category, "value") else str(item.category)
            by_cat.setdefault(cat, []).append(item)

        rendered: list[str] = []
        for cat in sorted(by_cat.keys()):
            rendered.append(f"### {cat.title()}")
            for item in by_cat[cat]:
                qty = self._format_shop_line(item)
                rendered.append(f"- [ ] {qty}")
                if item.notes:
                    rendered.append(f"  - {item.notes}")
            rendered.append("")
        return rendered

    @staticmethod
    def _format_shop_line(item) -> str:
        qty = item.quantity.strip()
        unit = item.unit.strip()
        if unit and unit not in qty:
            qty = f"{qty} {unit}".strip()
        if qty:
            return f"{item.name} — {qty}"
        return item.name

    def _format_ingredients(self, ingredients) -> str:
        return "\n".join(
            f"- {i.name}: {i.quantity} {i.unit} ({i.category})".strip()
            for i in ingredients
        )

from __future__ import annotations

import logging
from collections import Counter

from pydantic import BaseModel, Field

from mealprepper.config import get_settings
from mealprepper.context.budget import CallType, load_context_budget
from mealprepper.context.prompt_builder import PromptBuilder
from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError
from mealprepper.models.meals import Ingredient, PlannedMeal
from mealprepper.models.plans import WeeklyPlan

logger = logging.getLogger(__name__)


class SynergyReport(BaseModel):
    shared_ingredients: list[str] = Field(default_factory=list)
    waste_risks: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    notes: str = ""


class IngredientSynergySkill:
    """Analyze and optimize ingredient overlap across the week's meals."""

    SYSTEM = """You minimize food waste by maximizing ingredient overlap across a family's weekly meals.
Identify shared produce/proteins, flag items used only once, and suggest swaps to reuse leftovers."""

    def __init__(self, llm: OllamaClient | None = None) -> None:
        settings = get_settings()
        budget = load_context_budget(settings)
        self.llm = llm or OllamaClient(settings=settings, budget=budget)
        self.budget = budget

    def analyze(self, plan: WeeklyPlan) -> SynergyReport:
        counts = Counter(plan.ingredient_names())
        shared = [name for name, c in counts.items() if c >= 2]
        singles = [name for name, c in counts.items() if c == 1]

        try:
            builder = PromptBuilder(
                budget=self.budget,
                call_type=CallType.WEEK_ORGANIZER,
                system=self.SYSTEM,
                task="Analyze this week's meals for ingredient synergy.",
            )
            builder.add_section(
                "Frequency",
                f"Shared ingredients (2+ uses): {', '.join(shared) or 'none'}\n"
                f"Single-use ingredients: {', '.join(singles[:20]) or 'none'}",
                priority=10,
            )
            builder.add_section("Meals", self._meal_summary(plan.meals), priority=20)
            builder.add_section(
                "Output",
                "Return JSON: shared_ingredients, waste_risks, suggestions (array), notes.",
                priority=5,
            )

            return self.llm.chat_json(
                builder.build_messages(),
                SynergyReport,
                call_type=CallType.WEEK_ORGANIZER,
            )
        except (OllamaUnavailableError, ValueError) as exc:
            logger.warning("Synergy LLM failed: %s", exc)
            return SynergyReport(
                shared_ingredients=shared,
                waste_risks=singles[:10],
                suggestions=[
                    "Batch cook grains/proteins on Saturday bulk prep block.",
                    "Plan 2 dinners around the same vegetable (e.g. broccoli).",
                ],
                notes="Fallback synergy analysis based on ingredient frequency.",
            )

    def consolidate_ingredients(self, meals: list[PlannedMeal]) -> list[Ingredient]:
        merged: dict[str, Ingredient] = {}
        for meal in meals:
            for ing in meal.recipe.ingredients:
                key = ing.name.lower().strip()
                if key not in merged:
                    merged[key] = ing.model_copy()
                else:
                    existing = merged[key]
                    if ing.quantity:
                        existing.quantity = f"{existing.quantity}, {ing.quantity}".strip(", ")
        return list(merged.values())

    def _meal_summary(self, meals: list[PlannedMeal]) -> str:
        lines = []
        for m in meals[:21]:
            ings = ", ".join(i.name for i in m.recipe.ingredients[:6])
            lines.append(f"- {m.day} {m.meal_block}: {m.recipe.title} [{ings}]")
        return "\n".join(lines)

    def apply_synergy_notes(self, plan: WeeklyPlan, report: SynergyReport) -> WeeklyPlan:
        plan.synergy_notes = report.notes or "; ".join(report.suggestions)
        if report.suggestions:
            plan.playbook_markdown += "\n\n## Ingredient Synergy\n"
            for s in report.suggestions:
                plan.playbook_markdown += f"- {s}\n"
        return plan

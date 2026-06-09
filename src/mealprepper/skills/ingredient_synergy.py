from __future__ import annotations

import logging
from collections import Counter

from pydantic import BaseModel, Field, field_validator

from mealprepper.config import get_settings
from mealprepper.context.budget import CallType, load_context_budget
from mealprepper.context.prompt_builder import PromptBuilder
from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError
from mealprepper.models.meals import Ingredient, PlannedMeal
from mealprepper.models.plans import WeeklyPlan

logger = logging.getLogger(__name__)


def _normalize_synergy_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_normalize_synergy_text(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "description", "suggestion", "message", "note", "content", "detail"):
            if key in value and value[key]:
                text = _normalize_synergy_text(value[key])
                if text:
                    return text
        label = str(value.get("type", "")).replace("_", " ").strip().title()
        body_parts = [
            str(item).strip()
            for item in value.values()
            if isinstance(item, str) and item.strip()
        ]
        if label and body_parts:
            return f"{label}: {body_parts[0]}"
        if body_parts:
            return body_parts[0]
        if label:
            return label
        return str(value).strip()
    return str(value).strip()


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [
            line.strip().lstrip("0123456789.) ")
            for line in value.replace(";", "\n").splitlines()
            if line.strip()
        ]
    if not isinstance(value, list):
        text = _normalize_synergy_text(value)
        return [text] if text else []
    items: list[str] = []
    for item in value:
        text = _normalize_synergy_text(item)
        if text:
            items.append(text)
    return items


class SynergyReport(BaseModel):
    shared_ingredients: list[str] = Field(default_factory=list)
    waste_risks: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("shared_ingredients", "waste_risks", "suggestions", mode="before")
    @classmethod
    def normalize_list_fields(cls, value: object) -> list[str]:
        return _normalize_string_list(value)

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: object) -> str:
        return _normalize_synergy_text(value)


class IngredientSynergySkill:
    """Analyze and optimize ingredient overlap across the week's meals."""

    SYSTEM = """You minimize food waste and daily kitchen complexity by maximizing ingredient overlap.
Identify shared produce/proteins, flag single-use items, and suggest concrete reuse:
cook extra rice/grains for the next night, batch roast vegetables on Saturday for weekday dinners,
turn Monday dinner into Tuesday lunch, and align toddler lunches with dinner staples when possible."""

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
                "Return JSON with string fields only: "
                "shared_ingredients (array of strings), waste_risks (array of strings), "
                "suggestions (array of plain-text strings), notes (single string).",
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
        from mealprepper.skills.grocery_normalizer import repair_ingredient

        merged: dict[str, Ingredient] = {}
        for meal in meals:
            for raw in meal.recipe.ingredients:
                ing = repair_ingredient(raw)
                if ing is None:
                    continue
                key = ing.name.lower().strip()
                if key not in merged:
                    merged[key] = ing.model_copy()
                else:
                    existing = merged[key]
                    qty = " ".join(part for part in [ing.quantity, ing.unit] if part).strip()
                    if qty:
                        existing.quantity = f"{existing.quantity}, {qty}".strip(", ")
        return list(merged.values())

    def _meal_summary(self, meals: list[PlannedMeal]) -> str:
        lines = []
        for m in meals[:21]:
            ings = ", ".join(i.name for i in m.recipe.ingredients[:6])
            lines.append(f"- {m.day} {m.meal_block}: {m.recipe.title} [{ings}]")
        return "\n".join(lines)

    def apply_synergy_notes(self, plan: WeeklyPlan, report: SynergyReport) -> WeeklyPlan:
        plan.synergy_notes = report.notes or "; ".join(report.suggestions)
        plan.synergy_suggestions = list(report.suggestions)
        if report.suggestions:
            plan.playbook_markdown += "\n\n## Ingredient Synergy\n"
            for s in report.suggestions:
                plan.playbook_markdown += f"- {s}\n"
        return plan

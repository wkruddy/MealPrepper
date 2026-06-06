from __future__ import annotations

import logging
from datetime import date, timedelta

from pydantic import BaseModel, Field

from mealprepper.config import get_settings
from mealprepper.context.budget import CallType, ContextBudget, load_context_budget
from mealprepper.context.prompt_builder import PromptBuilder
from mealprepper.index.meal_index import MealIndex
from mealprepper.index.plan_index import PlanIndex
from mealprepper.index.preference_index import PreferenceIndex
from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError
from mealprepper.models.family import FamilyProfile
from mealprepper.models.feedback import PreferenceProfile
from mealprepper.models.meals import MealRecipe, PlannedMeal, RecipeStep
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

WEEKDAY_SCHOOL_BLOCKS = {
    "monday": ["toddler_school_lunch", "toddler_breakfast", "adult_breakfast", "adult_lunch", "adult_dinner", "infant_blw"],
    "tuesday": ["toddler_school_lunch", "toddler_breakfast", "adult_breakfast", "adult_lunch", "adult_dinner", "infant_blw"],
    "wednesday": ["toddler_school_lunch", "toddler_breakfast", "adult_breakfast", "adult_lunch", "adult_dinner", "infant_blw"],
    "thursday": ["toddler_school_lunch", "toddler_breakfast", "adult_breakfast", "adult_lunch", "adult_dinner", "infant_blw"],
    "friday": ["toddler_school_lunch", "toddler_breakfast", "adult_breakfast", "adult_lunch", "adult_dinner", "infant_blw"],
    "saturday": ["toddler_weekend_lunch", "toddler_breakfast", "adult_breakfast", "adult_lunch", "adult_dinner", "infant_blw", "bulk_meal_prep"],
    "sunday": ["toddler_weekend_lunch", "toddler_breakfast", "adult_breakfast", "adult_lunch", "adult_dinner", "infant_blw"],
}


class WeekMealOutline(BaseModel):
    day: str
    meal_block: str
    title: str
    key_ingredients: list[str] = Field(default_factory=list)
    prep_minutes: int = 30


class MealFinderSkill:
    """Find age-appropriate meals aligned with family constraints."""

    SYSTEM = """You are the MealPrepper meal finder for a family with a toddler (no spicy, quick dinners),
two adults, and a 7.5mo infant doing baby-led weaning. Users are competent cooks.

Return practical, real meals — not generic placeholders. Reuse ingredients across the week when possible.
Adult dinners must be ready within 45 minutes total prep+cook. Toddler eats similar to adults (mild seasoning).
Include BLW-safe finger food options for infant blocks."""

    def __init__(
        self,
        llm: OllamaClient | None = None,
        store: SQLiteStore | None = None,
        budget: ContextBudget | None = None,
        meal_index: MealIndex | None = None,
        preference_index: PreferenceIndex | None = None,
        plan_index: PlanIndex | None = None,
    ) -> None:
        self.settings = get_settings()
        self.budget = budget or load_context_budget(self.settings)
        self.llm = llm or OllamaClient(settings=self.settings, budget=self.budget)
        self.store = store or SQLiteStore(settings=self.settings)
        self.meal_index = meal_index or MealIndex(settings=self.settings)
        self.preference_index = preference_index or PreferenceIndex(settings=self.settings)
        self.plan_index = plan_index or PlanIndex(settings=self.settings)
        cfg = self.settings.merged_config().get("index", {})
        self.meal_top_k = int(cfg.get("meal_top_k", 5))
        self.feedback_top_k = int(cfg.get("feedback_top_k", 8))

    def find_week_outline(
        self,
        family: FamilyProfile,
        preferences: PreferenceProfile,
        week_start: date,
    ) -> list[WeekMealOutline]:
        week_end = week_start + timedelta(days=6)
        compact_prefs = self.store.get_compact_preferences() if self.store else preferences
        past_meals = self.meal_index.search("dinner lunch breakfast", top_k=self.meal_top_k)
        past_meals_text = self.meal_index.format_for_prompt(past_meals)
        feedback_ctx = self.preference_index.relevant_for_block("adult_dinner", top_k=self.feedback_top_k)
        plan_ctx = self.plan_index.similar_to_week(week_start, top_k=2)

        builder = PromptBuilder(
            budget=self.budget,
            call_type=CallType.MEAL_FINDER,
            system=self.SYSTEM,
            task=f"Plan meals for {week_start} to {week_end}.",
        )
        builder.add_section("Family", self._family_context(family), priority=10)
        builder.add_section("Preferences", compact_prefs.to_prompt_context(), priority=20)
        if feedback_ctx:
            builder.add_section("Recent feedback", feedback_ctx, priority=25)
        builder.add_section("Past meals to reuse or vary", past_meals_text, priority=40)
        if plan_ctx:
            builder.add_section("Past weeks", plan_ctx, priority=50)
        builder.add_section(
            "Requirements",
            """Meal blocks per day:
- Weekdays: toddler school lunch, toddler breakfast, adult breakfast/lunch/dinner, infant BLW
- Weekends: toddler home lunch instead of school lunch; optional bulk prep Saturday

Return JSON array of objects with: day, meal_block, title, key_ingredients (array), prep_minutes.
Cover every required block for each day. Avoid disliked meals/ingredients.""",
            priority=15,
        )

        messages = builder.build_messages()
        try:
            return self.llm.chat_json_list(
                messages,
                WeekMealOutline,
                call_type=CallType.MEAL_FINDER,
            )
        except (OllamaUnavailableError, ValueError) as exc:
            logger.warning("LLM meal finder failed, using template week: %s", exc)
            return self._fallback_outline(week_start)

    def expand_recipe(self, outline: WeekMealOutline, family: FamilyProfile) -> MealRecipe:
        relevant = self.meal_index.search(
            outline.title,
            meal_block=outline.meal_block,
            top_k=2,
        )
        past_hint = self.meal_index.format_for_prompt(relevant) if relevant else ""

        builder = PromptBuilder(
            budget=self.budget,
            call_type=CallType.RECIPE_EXPAND,
            system=self.SYSTEM,
            task=f"Expand this meal into a full recipe JSON object:\nTitle: {outline.title}\nDay: {outline.day}, Block: {outline.meal_block}\nKey ingredients: {', '.join(outline.key_ingredients)}",
        )
        builder.add_section(
            "Fields",
            "title, description, prep_minutes, cook_minutes, servings, ingredients (name, quantity, unit, category), "
            "steps (order, instruction, duration_minutes), tags, infant_guidance, toddler_modifications.",
            priority=10,
        )
        builder.add_section(
            "Family context",
            "toddler no spicy; infant BLW 7.5mo; adults competent cooks.",
            priority=20,
        )
        if past_hint:
            builder.add_section("Similar past recipes", past_hint, priority=40)

        try:
            return self.llm.chat_json(
                builder.build_messages(),
                MealRecipe,
                call_type=CallType.RECIPE_EXPAND,
            )
        except (OllamaUnavailableError, ValueError):
            return self._fallback_recipe(outline)

    def outline_to_planned_meals(
        self,
        outlines: list[WeekMealOutline],
        family: FamilyProfile,
    ) -> list[PlannedMeal]:
        meals: list[PlannedMeal] = []
        for outline in outlines:
            recipe = self.expand_recipe(outline, family)
            member_ids = self._members_for_block(outline.meal_block, family)
            meals.append(
                PlannedMeal(
                    meal_block=outline.meal_block,
                    day=outline.day,
                    recipe=recipe,
                    member_ids=member_ids,
                )
            )
        return meals

    def _family_context(self, family: FamilyProfile) -> str:
        lines = []
        for m in family.members:
            lines.append(f"- {m.name} ({m.role.value}): {m.notes}; constraints={m.constraints}")
        return "\n".join(lines)

    def _members_for_block(self, block: str, family: FamilyProfile) -> list[str]:
        if block.startswith("toddler"):
            t = family.toddler()
            return [t.id] if t else []
        if block == "infant_blw":
            i = family.infant()
            return [i.id] if i else []
        if block.startswith("adult") or block == "bulk_meal_prep":
            return [a.id for a in family.adults()]
        return family.member_ids()

    def _fallback_outline(self, week_start: date) -> list[WeekMealOutline]:
        templates = [
            ("adult_dinner", "Sheet Pan Lemon Herb Chicken", ["chicken thighs", "broccoli", "potatoes"], 35),
            ("adult_lunch", "Mediterranean Grain Bowls", ["quinoa", "cucumber", "chickpeas", "feta"], 20),
            ("adult_breakfast", "Overnight Oats", ["oats", "yogurt", "berries"], 10),
            ("toddler_school_lunch", "Turkey & Cheese Roll-ups", ["turkey", "tortilla", "cheese"], 10),
            ("toddler_weekend_lunch", "Mini Quesadillas", ["tortilla", "cheese", "beans"], 15),
            ("toddler_breakfast", "Banana Pancake Bites", ["banana", "eggs", "oats"], 15),
            ("infant_blw", "Steamed Broccoli & Avocado Strips", ["broccoli", "avocado"], 10),
            ("bulk_meal_prep", "Batch Cooked Rice & Roasted Veg", ["rice", "sweet potato", "zucchini"], 40),
        ]
        outlines: list[WeekMealOutline] = []
        for i in range(7):
            day = DAYS[i]
            blocks = WEEKDAY_SCHOOL_BLOCKS.get(day, [])
            for j, block in enumerate(blocks):
                tmpl = templates[j % len(templates)]
                outlines.append(
                    WeekMealOutline(
                        day=day,
                        meal_block=block,
                        title=tmpl[1] if block == tmpl[0] else f"{tmpl[1]} ({block.replace('_', ' ')})",
                        key_ingredients=list(tmpl[2]),
                        prep_minutes=tmpl[3],
                    )
                )
        return outlines

    def _fallback_recipe(self, outline: WeekMealOutline) -> MealRecipe:
        from mealprepper.models.meals import Ingredient

        ingredients = [
            Ingredient(name=name, quantity="1", unit="portion", category="other")
            for name in outline.key_ingredients
        ]
        return MealRecipe(
            title=outline.title,
            description=f"Fallback recipe for {outline.meal_block}",
            prep_minutes=outline.prep_minutes,
            cook_minutes=20,
            ingredients=ingredients,
            steps=[RecipeStep(order=1, instruction="Prepare and serve.", duration_minutes=20)],
            tags=["fallback"],
            infant_guidance="Offer soft, finger-sized pieces appropriate for BLW.",
            toddler_modifications="Skip spice; cut into bite-sized pieces.",
        )

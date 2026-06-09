from __future__ import annotations

import logging
from datetime import date, timedelta

from mealprepper.config import get_settings
from mealprepper.context.budget import CallType, ContextBudget, load_context_budget
from mealprepper.context.prompt_builder import PromptBuilder
from mealprepper.index.meal_index import MealIndex
from mealprepper.index.plan_index import PlanIndex
from mealprepper.index.preference_index import PreferenceIndex
from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError, _extract_json
from mealprepper.models.family import FamilyProfile
from mealprepper.models.feedback import PreferenceProfile
from mealprepper.models.meals import MealRecipe, PlannedMeal, RecipeStep
from mealprepper.skills.blw_safety import BLWSafety
from mealprepper.skills.dish_history import DishHistorySkill
from mealprepper.skills.food_groups import FoodGroupsSkill
from mealprepper.skills.meal_blocks import DAYS, WeekMealOutline
from mealprepper.skills.meal_catalog import MealCatalog
from mealprepper.skills.recipe_repository import RecipeRepositorySkill
from mealprepper.skills.pantry_config import _normalize_name
from mealprepper.skills.ingredient_cohesion import (
    align_bulk_prep_to_anchors,
    cohesion_prompt_lines,
    compute_anchor_ingredients,
)
from mealprepper.skills.week_outline import finalize_outline, parse_outline_items, required_slots
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)

MEAL_BLOCK_RULES = """Assign meals that match WHO eats them:
- toddler_school_lunch: portable packed lunches (wraps, pinwheels, bento boxes) — not hot dinners
- toddler_weekend_lunch: simple sit-down lunches for home
- toddler_breakfast: quick kid breakfasts (pancakes, eggs, yogurt, oatmeal)
- adult_breakfast: adult breakfasts (may differ from toddler breakfast)
- adult_lunch: work lunches, grain bowls, soups, salads, leftovers
- adult_dinner: main family dinners (protein + veg + starch); toddler eats a mild portion at home
- infant_blw: ONLY age-appropriate BLW finger foods — never adult meals repurposed
- bulk_meal_prep: batch components for the week (grains, proteins, roasted veg)"""


class MealFinderSkill:
    """Find age-appropriate meals aligned with family constraints."""

    SYSTEM = """You are the MealPrepper meal finder for a family with a toddler (no spicy, quick dinners),
two adults, and an infant doing baby-led weaning. Users are competent cooks.

Return practical, real meals — not generic placeholders. Prioritize easy weeks: fewer unique
ingredients per day, shared components across nights (cook extra rice Tuesday for Wednesday's bowl),
and Saturday bulk prep that feeds weekday dinners.
Reuse ingredients across the week when sensible, but do NOT repeat the same meal title more than
twice per meal block in one week.
Adult dinners must be ready within 45 minutes total prep+cook."""

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
        cfg = self.settings.merged_config()
        index_cfg = cfg.get("index", {})
        planning_cfg = cfg.get("planning", {})
        self.meal_top_k = int(index_cfg.get("meal_top_k", 5))
        self.feedback_top_k = int(index_cfg.get("feedback_top_k", 8))
        self.max_meal_repeat = int(planning_cfg.get("max_meal_repeat_days", 2))
        self.recipe_top_k = int(index_cfg.get("recipe_top_k", 6))
        self.catalog = MealCatalog(self.settings)
        self.dish_history = DishHistorySkill(store=self.store, settings=self.settings)
        self.recipe_repo = RecipeRepositorySkill(store=self.store, llm=self.llm, settings=self.settings)
        from mealprepper.skills.cook_efficiency import CookEfficiencyConfig

        self.cook_efficiency = CookEfficiencyConfig.from_settings(self.settings)
        self.food_groups = FoodGroupsSkill(self.settings)
        cohesion_cfg = planning_cfg.get("ingredient_cohesion", {})
        self.cohesion_enabled = bool(cohesion_cfg.get("enabled", True))
        self.cohesion_top_n = int(cohesion_cfg.get("anchor_top_n", 10))
        self.cohesion_min_mentions = int(cohesion_cfg.get("min_mentions", 2))
        self._week_anchors: list[str] = []

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
        feedback_ctx = self._build_feedback_context()
        recent_excluded = self.dish_history.recent_titles_by_block(week_start)
        excluded_normalized = self.dish_history.normalized_exclusions(recent_excluded)
        exclusion_text = self.dish_history.format_exclusions(recent_excluded)
        plan_ctx = self.plan_index.similar_to_week(week_start, top_k=2)
        blw = BLWSafety(family, self.settings)

        builder = PromptBuilder(
            budget=self.budget,
            call_type=CallType.MEAL_FINDER,
            system=self.SYSTEM,
            task=f"Plan meals for week starting {week_start} ({week_start} to {week_end}).",
        )
        builder.add_section("Family", self._family_context(family), priority=10)
        builder.add_section("Meal block rules", MEAL_BLOCK_RULES, priority=12)
        if self.cook_efficiency.enabled:
            builder.add_section(
                "Cook efficiency",
                "Minimize cook sessions — this matters more than variety.\n"
                "- Plan ~4 unique adult dinners for the week; repeat them on other nights.\n"
                "- Each dinner should yield next-day adult lunch leftovers (same title).\n"
                "- Batch-friendly meals: sheet pan, stir fry, roast chicken, grain bowls, tacos.\n"
                "- Seafood leftovers keep ~2 days; poultry ~3; most veg/grain dishes ~4-5.\n"
                "- Do not plan leftover seafood more than 1 day after cooking.\n"
                "- Saturday bulk_meal_prep should prep components reused in weekday meals.\n"
                "- When a dinner uses rice/grains/roasted veg, plan the next night to reuse leftovers.\n"
                "- Pick dinners that share proteins and produce — not 7 unrelated cuisines.\n"
                f"- Same title may repeat up to {self.max_meal_repeat} times per block.",
                priority=13,
            )
        else:
            builder.add_section(
                "Variety",
                f"Use at least 3 different meals per meal block across the week. "
                f"Never repeat the same title more than {self.max_meal_repeat} times per meal block. "
                "Do not serve identical dinners every night. Invent original titles — do not copy example lists.",
                priority=13,
            )
        builder.add_section("BLW safety", blw.prompt_context(), priority=14)
        builder.add_section(
            "Food groups (toddler meals)",
            self.food_groups.prompt_context(strict=True),
            priority=14,
        )
        builder.add_section(
            "Food groups (adult meals)",
            self.food_groups.prompt_context(strict=False),
            priority=15,
        )
        builder.add_section("Preferences", compact_prefs.to_prompt_context(), priority=20)
        if exclusion_text:
            builder.add_section("Recently served (avoid repeating)", exclusion_text, priority=22)
        if feedback_ctx:
            builder.add_section("Recent feedback", feedback_ctx, priority=25)
        builder.add_section("Block style guide", self.catalog.prompt_style_guide(), priority=35)
        saved_recipes = self.recipe_repo.search_for_planning()
        if saved_recipes:
            builder.add_section(
                "Family recipe library",
                self.recipe_repo.format_for_outline(saved_recipes),
                priority=33,
            )
        builder.add_section("Past meals to reuse or vary", past_meals_text, priority=40)
        if plan_ctx:
            builder.add_section("Past weeks", plan_ctx, priority=50)
        builder.add_section(
            "Output format",
            """Return a JSON array. Each object MUST include:
- day: lowercase weekday name (monday..sunday) — NOT an ISO date
- meal_block: exact block id (e.g. toddler_school_lunch, adult_dinner, infant_blw)
- title: meal name appropriate for that block
- key_ingredients: array of strings
- prep_minutes: integer

Cover every required block for each day.""",
            priority=15,
        )

        messages = builder.build_messages()
        logger.info(
            "Generating week outline for %s — %s (model=%s)",
            week_start,
            week_end,
            self.llm.model,
        )
        try:
            content = self.llm.chat(
                messages,
                json_mode=True,
                call_type=CallType.MEAL_FINDER,
            )
            parsed = _extract_json(content)
            if not isinstance(parsed, list):
                raise ValueError(f"Expected JSON array, got {type(parsed)}")
            outlines = parse_outline_items(parsed, week_start)
            if len(outlines) < len(required_slots()) // 2:
                raise ValueError(f"Too few valid outline items ({len(outlines)})")
            outlines = finalize_outline(
                outlines,
                week_start,
                self.catalog,
                self.max_meal_repeat,
                excluded_by_block=excluded_normalized,
            )
            outlines = self._apply_ingredient_cohesion(outlines)
            logger.info("Week outline ready: %d meal slots", len(outlines))
            return outlines
        except (OllamaUnavailableError, ValueError) as exc:
            logger.warning("LLM meal finder failed, using catalog fallback: %s", exc)
            fallback = self.catalog.build_fallback_outline(week_start, self.max_meal_repeat)
            fallback = finalize_outline(
                fallback,
                week_start,
                self.catalog,
                self.max_meal_repeat,
                excluded_by_block=excluded_normalized,
            )
            return self._apply_ingredient_cohesion(fallback)

    def _apply_ingredient_cohesion(self, outlines: list[WeekMealOutline]) -> list[WeekMealOutline]:
        if not self.cohesion_enabled:
            self._week_anchors = []
            return outlines
        anchors = compute_anchor_ingredients(
            outlines,
            top_n=self.cohesion_top_n,
            min_mentions=self.cohesion_min_mentions,
        )
        self._week_anchors = anchors
        return align_bulk_prep_to_anchors(outlines, anchors)

    def expand_recipe(self, outline: WeekMealOutline, family: FamilyProfile) -> MealRecipe:
        blw = BLWSafety(family, self.settings)
        saved = self.recipe_repo.match_outline(outline)
        if saved and saved.has_full_recipe():
            logger.info("Using saved family recipe for %s: %s", outline.meal_block, saved.title)
            recipe = saved.to_meal_recipe(outline.title)
            return self._apply_block_safety(recipe, outline, blw)

        relevant = self.meal_index.search(
            outline.title,
            meal_block=outline.meal_block,
            top_k=2,
        )
        past_hint = self.meal_index.format_for_prompt(relevant) if relevant else ""
        saved_matches = self.recipe_repo.search(outline.title, meal_block=outline.meal_block, top_k=2)
        saved_hint = self.recipe_repo.format_for_prompt(saved_matches) if saved_matches else ""

        builder = PromptBuilder(
            budget=self.budget,
            call_type=CallType.RECIPE_EXPAND,
            system=self.SYSTEM,
            task=(
                f"Expand this meal into a full recipe JSON object:\n"
                f"Title: {outline.title}\n"
                f"Day: {outline.day}, Block: {outline.meal_block}\n"
                f"Key ingredients: {', '.join(outline.key_ingredients)}"
            ),
        )
        builder.add_section("Meal block rules", MEAL_BLOCK_RULES, priority=5)
        builder.add_section(
            "Fields",
            "title, description, prep_minutes, cook_minutes, servings, ingredients (name, quantity, unit, category), "
            "steps (order, instruction, duration_minutes), tags, food_groups (carb/protein/veggie/fruit/fat → ingredient), "
            "infant_guidance, toddler_modifications.\n"
            "Ingredient names must be food only — put amounts in quantity and units in unit (never '2 cups rice' in name).",
            priority=10,
        )
        cohesion = cohesion_prompt_lines(self._week_anchors)
        if cohesion:
            builder.add_section("Week cohesion", cohesion, priority=8)
        if self.food_groups.strict_for_block(outline.meal_block):
            builder.add_section(
                "Food groups",
                self.food_groups.prompt_context(strict=True),
                priority=12,
            )
        elif self.food_groups.note_for_block(outline.meal_block):
            builder.add_section(
                "Food groups",
                self.food_groups.prompt_context(strict=False),
                priority=12,
            )
        builder.add_section("Family context", self._family_context(family), priority=20)
        if outline.meal_block == "infant_blw":
            builder.add_section("BLW safety", blw.prompt_context(), priority=15)
        if past_hint:
            builder.add_section("Similar past recipes", past_hint, priority=40)
        if saved_hint:
            builder.add_section("Saved family recipes", saved_hint, priority=38)

        try:
            recipe = self.llm.chat_json(
                builder.build_messages(),
                MealRecipe,
                call_type=CallType.RECIPE_EXPAND,
            )
            return self._apply_block_safety(recipe, outline, blw)
        except (OllamaUnavailableError, ValueError):
            return self._fallback_recipe(outline, blw)

    def outline_to_planned_meals(
        self,
        outlines: list[WeekMealOutline],
        family: FamilyProfile,
    ) -> list[PlannedMeal]:
        if self.cohesion_enabled:
            self._week_anchors = compute_anchor_ingredients(
                outlines,
                top_n=self.cohesion_top_n,
                min_mentions=self.cohesion_min_mentions,
            )
        meals: list[PlannedMeal] = []
        recipe_cache: dict[tuple[str, str], MealRecipe] = {}
        total = len(outlines)
        for index, outline in enumerate(outlines, start=1):
            source_key = (
                (outline.reuse_of_day, outline.reuse_of_block)
                if outline.reuse_of_day and outline.reuse_of_block
                else None
            )
            if source_key and source_key in recipe_cache:
                cached = recipe_cache[source_key]
                if _normalize_name(cached.title) != _normalize_name(outline.title):
                    saved = self.recipe_repo.match_outline(outline)
                    if saved and saved.has_full_recipe():
                        recipe = saved.to_meal_recipe(outline.title)
                        logger.info(
                            "Resolved reused slot %d/%d: %s via saved recipe %s",
                            index,
                            total,
                            outline.title,
                            saved.title,
                        )
                    else:
                        recipe = self.expand_recipe(outline, family)
                        logger.info(
                            "Re-expanded reused slot %d/%d: %s (title differs from %s)",
                            index,
                            total,
                            outline.title,
                            cached.title,
                        )
                else:
                    recipe = cached.model_copy(deep=True)
                    recipe.title = outline.title
                    logger.info(
                        "Reusing recipe %d/%d: %s (%s, %s) from %s %s",
                        index,
                        total,
                        outline.title,
                        outline.day,
                        outline.meal_block,
                        outline.reuse_of_day,
                        outline.reuse_of_block,
                    )
            else:
                logger.info(
                    "Expanding recipe %d/%d: %s (%s, %s)",
                    index,
                    total,
                    outline.title,
                    outline.day,
                    outline.meal_block,
                )
                recipe = self.expand_recipe(outline, family)
                recipe_cache[(outline.day, outline.meal_block)] = recipe

            member_ids = self._members_for_block(outline.meal_block, family)
            meals.append(
                PlannedMeal(
                    meal_block=outline.meal_block,
                    day=outline.day,
                    recipe=recipe,
                    member_ids=member_ids,
                    cook_source_day=outline.reuse_of_day,
                    cook_source_block=outline.reuse_of_block,
                    cook_note=outline.cook_note,
                )
            )
        return meals

    def _apply_block_safety(
        self,
        recipe: MealRecipe,
        outline: WeekMealOutline,
        blw: BLWSafety,
    ) -> MealRecipe:
        recipe = self._sanitize_recipe_ingredients(recipe)
        ingredient_names = [i.name for i in recipe.ingredients] or outline.key_ingredients
        if outline.meal_block == "infant_blw":
            warnings, blocked, guidance = blw.validate_meal(recipe.title, ingredient_names)
            if warnings:
                logger.warning("BLW check for %s: %s", recipe.title, "; ".join(warnings))
            recipe.infant_guidance = guidance
            if blocked:
                recipe.ingredients = [
                    ing
                    for ing in recipe.ingredients
                    if not any(b in ing.name.lower() for b in blocked)
                ]
        elif outline.meal_block.startswith("toddler"):
            if not recipe.toddler_modifications:
                recipe.toddler_modifications = "No spice; bite-sized pieces; check salt."
        return self.food_groups.annotate_recipe(recipe, outline.meal_block)

    def _build_feedback_context(self) -> str:
        """Aggregate indexed feedback, preference summary, and recent comments."""
        lines: list[str] = []
        for block in ("adult_dinner", "adult_lunch", "toddler_school_lunch"):
            block_ctx = self.preference_index.relevant_for_block(block, top_k=self.feedback_top_k)
            if block_ctx:
                lines.append(block_ctx)

        summary = self.store.get_latest_preference_summary()
        if summary:
            lines.append(f"Learned preferences: {summary}")

        recent = self.store.list_recent_feedback(limit=8)
        if recent:
            comment_lines = []
            for fb in recent:
                comment = f" — {fb.comment}" if fb.comment and fb.comment != fb.meal_title else ""
                block = f" ({fb.meal_block})" if fb.meal_block else ""
                comment_lines.append(f"{fb.rating.value}: {fb.meal_title}{block}{comment}")
            lines.append("Recent ratings:\n" + "\n".join(comment_lines[:8]))

        return "\n".join(lines)

    def _family_context(self, family: FamilyProfile) -> str:
        lines = []
        for member in family.members:
            age = ""
            if member.age_months is not None:
                age = f", {member.age_months}mo"
            elif member.age_years is not None:
                age = f", {member.age_years}y"
            lines.append(
                f"- {member.name} ({member.role.value}{age}): {member.notes}; constraints={member.constraints}"
            )
        return "\n".join(lines)

    def _members_for_block(self, block: str, family: FamilyProfile) -> list[str]:
        if block.startswith("toddler"):
            toddler = family.toddler()
            return [toddler.id] if toddler else []
        if block == "infant_blw":
            infant = family.infant()
            return [infant.id] if infant else []
        if block.startswith("adult") or block == "bulk_meal_prep":
            return [adult.id for adult in family.adults()]
        return family.member_ids()

    def _sanitize_recipe_ingredients(self, recipe: MealRecipe) -> MealRecipe:
        from mealprepper.skills.grocery_normalizer import repair_ingredient

        repaired = [fixed for ing in recipe.ingredients if (fixed := repair_ingredient(ing))]
        if repaired:
            recipe.ingredients = repaired
        return recipe

    def _fallback_recipe(self, outline: WeekMealOutline, blw: BLWSafety) -> MealRecipe:
        from mealprepper.models.meals import Ingredient

        ingredients = [
            Ingredient(name=name, quantity="1", unit="portion", category="other")
            for name in outline.key_ingredients
        ]
        infant_guidance = ""
        toddler_modifications = ""
        if outline.meal_block == "infant_blw":
            infant_guidance = blw.infant_guidance_for_outline(outline.title, outline.key_ingredients)
        elif outline.meal_block.startswith("toddler"):
            toddler_modifications = "No spice; cut into bite-sized pieces."
        return MealRecipe(
            title=outline.title,
            description=f"{outline.meal_block.replace('_', ' ').title()} — {outline.title}",
            prep_minutes=outline.prep_minutes,
            cook_minutes=20,
            ingredients=ingredients,
            steps=[RecipeStep(order=1, instruction="Prepare and serve.", duration_minutes=20)],
            tags=["fallback"],
            infant_guidance=infant_guidance,
            toddler_modifications=toddler_modifications,
        )

from __future__ import annotations

import logging
from datetime import date

from mealprepper.agents.base import AgentResult, BaseAgent
from mealprepper.config import get_settings
from mealprepper.context.budget import load_context_budget
from mealprepper.models.plans import PlanStatus, WeeklyPlan
from mealprepper.services.family_prompts import weekly_meals_system_prompt
from mealprepper.services.family_resolver import FamilyContext, FamilyResolver
from mealprepper.skills.food_shelf_life import FoodShelfLifeSkill
from mealprepper.skills.ingredient_synergy import IngredientSynergySkill
from mealprepper.skills.meal_finder import MealFinderSkill
from mealprepper.skills.week_organizer import WeekOrganizerSkill
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class WeeklyMealsAgent(BaseAgent):
    name = "weekly_meals"

    def __init__(
        self,
        store: SQLiteStore | None = None,
        family_context: FamilyContext | None = None,
        meal_finder: MealFinderSkill | None = None,
        week_organizer: WeekOrganizerSkill | None = None,
        synergy: IngredientSynergySkill | None = None,
        shelf_life: FoodShelfLifeSkill | None = None,
        **kwargs,
    ) -> None:
        self.settings = get_settings()
        self.budget = load_context_budget(self.settings)
        self.store = store or SQLiteStore(settings=self.settings)
        self.family_context = family_context or FamilyResolver(
            db_path=self.store.db_path,
            settings=self.settings,
        ).for_family_id(self.store.family_id)
        self.system_prompt = weekly_meals_system_prompt(self.family_context)
        self.meal_finder = meal_finder or MealFinderSkill(
            store=self.store,
            budget=self.budget,
            family_context=self.family_context,
        )
        self.week_organizer = week_organizer or WeekOrganizerSkill(self.meal_finder)
        self.synergy = synergy or IngredientSynergySkill()
        self.shelf_life = shelf_life or FoodShelfLifeSkill()
        super().__init__(llm=self.meal_finder.llm, **kwargs)

    def _register_tools(self) -> None:
        self.register_tool("find_meals", "Find meal candidates for a week", self._find_meals)
        self.register_tool("organize_week", "Build structured weekly plan", self._organize_week)
        self.register_tool(
            "validate_shelf_life",
            "Check leftover timing for cooked meals",
            self._validate_shelf_life,
        )
        self.register_tool(
            "synergize_ingredients", "Optimize ingredient overlap", self._synergize
        )
        self.register_tool("load_preferences", "Load family preference profile", self._load_prefs)
        self.register_tool("save_plan", "Persist weekly plan to SQLite", self._save_plan)

    def plan_week(self, week_start: date | None = None) -> AgentResult:
        """Main orchestration loop for weekly planning."""
        prefs = self.run_tool("load_preferences")
        plan: WeeklyPlan = self.run_tool("organize_week", week_start=week_start, preferences=prefs)
        plan = self.run_tool("validate_shelf_life", plan=plan)
        plan = self.run_tool("synergize_ingredients", plan=plan)
        plan.status = PlanStatus.PENDING_APPROVAL
        plan = self.run_tool("save_plan", plan=plan)
        return AgentResult(
            success=True,
            message=f"Weekly plan created ({plan.week_start} — {plan.week_end}) with {len(plan.meals)} meals",
            data=plan,
        )

    def _find_meals(self, week_start: date | None = None, **_) -> list:
        prefs = self.store.get_compact_preferences()
        start = week_start or self.week_organizer.week_bounds()[0]
        return self.meal_finder.find_week_outline(self.family_context.profile, prefs, start)

    def _organize_week(self, week_start: date | None = None, preferences=None, **_) -> WeeklyPlan:
        prefs = preferences or self.store.get_compact_preferences()
        return self.week_organizer.organize_week(
            self.family_context.profile,
            prefs,
            week_start,
        )

    def _validate_shelf_life(self, plan: WeeklyPlan, **_) -> WeeklyPlan:
        audit = self.shelf_life.audit_plan(plan)
        if audit.warnings:
            logger.warning("Shelf life audit found %d issues", len(audit.warnings))
            existing = list(plan.synergy_suggestions)
            plan.synergy_suggestions = audit.warnings + existing
        return plan

    def _synergize(self, plan: WeeklyPlan, **_) -> WeeklyPlan:
        report = self.synergy.analyze(plan)
        return self.synergy.apply_synergy_notes(plan, report)

    def _load_prefs(self, **_) -> object:
        return self.store.get_compact_preferences()

    def _save_plan(self, plan: WeeklyPlan, **_) -> WeeklyPlan:
        return self.store.save_weekly_plan(plan)

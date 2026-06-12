from __future__ import annotations

import logging

from mealprepper.agents.base import AgentResult, BaseAgent
from mealprepper.models.grocery import GroceryList
from mealprepper.models.plans import WeeklyPlan
from mealprepper.services.family_resolver import FamilyContext, FamilyResolver
from mealprepper.skills.grocery_builder import GroceryBuilderSkill
from mealprepper.skills.ingredient_synergy import IngredientSynergySkill
from mealprepper.skills.inventory import InventorySkill
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


GROCERY_SYSTEM = """You are the Grocery List Agent for MealPrepper.

Your responsibilities:
- Build a consolidated shopping list from the approved weekly meal plan
- Communicate with the Weekly Meals Agent to maximize ingredient overlap
- Track inventory (spices/pantry — foundation for future smart shopping)
- Have the list ready by Sunday morning for weekend shopping

Available tools: load_plan, build_list, apply_inventory, save_list, render_list"""


class GroceryListAgent(BaseAgent):
    name = "grocery_list"
    system_prompt = GROCERY_SYSTEM

    def __init__(
        self,
        store: SQLiteStore | None = None,
        family_context: FamilyContext | None = None,
        builder: GroceryBuilderSkill | None = None,
        inventory: InventorySkill | None = None,
        **kwargs,
    ) -> None:
        self.store = store or SQLiteStore()
        self.family_context = family_context or FamilyResolver(
            db_path=self.store.db_path,
        ).for_family_id(self.store.family_id)
        self.builder = builder or GroceryBuilderSkill()
        self.inventory = inventory or InventorySkill(
            store=self.store,
            family_context=self.family_context,
        )
        super().__init__(**kwargs)

    def _register_tools(self) -> None:
        self.register_tool("load_plan", "Load weekly plan by ID or latest approved", self._load_plan)
        self.register_tool("build_list", "Build grocery list from plan", self._build_list)
        self.register_tool(
            "apply_inventory", "Remove items already in pantry", self._apply_inventory
        )
        self.register_tool("save_list", "Save grocery list to SQLite", self._save_list)
        self.register_tool("render_list", "Render markdown grocery list", self._render_list)

    def generate(self, plan_id: str | None = None) -> AgentResult:
        plan = self.run_tool("load_plan", plan_id=plan_id)
        if not plan:
            return AgentResult(success=False, message="No approved weekly plan found")
        grocery: GroceryList = self.run_tool("build_list", plan=plan)
        grocery.items = self.run_tool("apply_inventory", items=grocery.items)
        grocery = self.run_tool("save_list", grocery=grocery)
        text = self.run_tool("render_list", grocery=grocery)
        return AgentResult(
            success=True,
            message=f"Grocery list ready with {len(grocery.items)} items",
            data={"grocery": grocery, "markdown": text},
        )

    def _load_plan(self, plan_id: str | None = None, **_) -> WeeklyPlan | None:
        if plan_id:
            return self.store.get_weekly_plan(plan_id)
        from mealprepper.models.plans import PlanStatus

        plan = self.store.get_latest_plan(PlanStatus.APPROVED)
        if plan:
            return plan
        return self.store.get_latest_plan()

    def _build_list(self, plan: WeeklyPlan, **_) -> GroceryList:
        synergy = IngredientSynergySkill()
        report = synergy.analyze(plan)
        plan = synergy.apply_synergy_notes(plan, report)
        return self.builder.build(plan)

    def _apply_inventory(self, items, **_) -> list:
        return self.inventory.subtract_from_grocery(items)

    def _save_list(self, grocery: GroceryList, **_) -> GroceryList:
        return self.store.save_grocery_list(grocery)

    def _render_list(self, grocery: GroceryList, **_) -> str:
        return self.builder.render_text(grocery)

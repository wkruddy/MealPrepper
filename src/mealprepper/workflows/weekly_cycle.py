from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from mealprepper.agents.communications import CommunicationsAgent
from mealprepper.agents.grocery import GroceryListAgent
from mealprepper.agents.weekly_meals import WeeklyMealsAgent
from mealprepper.config import get_settings
from mealprepper.models.plans import PlanStatus
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class WeeklyCycleWorkflow:
    """Orchestrates: plan → approve → grocery → daily reminders → feedback."""

    def __init__(self, store: SQLiteStore | None = None) -> None:
        self.store = store or SQLiteStore()
        self.meals_agent = WeeklyMealsAgent(store=self.store)
        self.grocery_agent = GroceryListAgent(store=self.store)
        self.comms_agent = CommunicationsAgent(store=self.store)
        self.settings = get_settings()

    def run_plan_week(self, week_start: date | None = None, auto_approve: bool = False) -> dict:
        result = self.meals_agent.plan_week(week_start)
        plan = result.data
        output_dir = self.settings.data_dir / "plans"
        output_dir.mkdir(parents=True, exist_ok=True)
        playbook_path = output_dir / f"playbook-{plan.week_start.isoformat()}.md"
        playbook_path.write_text(plan.playbook_markdown, encoding="utf-8")
        logger.info("Playbook written to %s", playbook_path)

        approval = self.comms_agent.request_plan_approval(plan)
        if auto_approve or not self.settings.approval_required:
            self.comms_agent.approve_plan(plan.id or "", approved=True, response="auto-approved")
            plan.status = PlanStatus.APPROVED
            self.store.save_weekly_plan(plan)

        return {
            "plan": plan,
            "playbook_path": str(playbook_path),
            "approval": approval.data,
        }

    def run_generate_grocery(self, plan_id: str | None = None) -> dict:
        result = self.grocery_agent.generate(plan_id)
        if not result.success:
            return {"success": False, "message": result.message}
        data = result.data
        grocery = data["grocery"]
        markdown = data["markdown"]
        path = self.settings.data_dir / "grocery" / f"grocery-{grocery.week_label.replace(' ', '')}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return {"success": True, "grocery": grocery, "path": str(path), "markdown": markdown}

    def run_send_daily(self, target: date | None = None) -> dict:
        result = self.comms_agent.send_daily_reminder(target)
        return {"success": result.success, "message": result.message}

    def run_process_feedback(self) -> dict:
        result = self.comms_agent.process_feedback_batch()
        return {"success": result.success, "preferences": result.data}

    def run_full_weekly_cycle(self, week_start: date | None = None) -> dict:
        plan_result = self.run_plan_week(week_start)
        plan = plan_result["plan"]
        if plan.status == PlanStatus.APPROVED:
            grocery_result = self.run_generate_grocery(plan.id)
        else:
            grocery_result = {"success": False, "message": "Awaiting approval before grocery list"}
        return {"plan": plan_result, "grocery": grocery_result}

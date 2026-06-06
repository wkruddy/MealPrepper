from __future__ import annotations

import logging
from datetime import date

from mealprepper.agents.communications import CommunicationsAgent
from mealprepper.agents.grocery import GroceryListAgent
from mealprepper.agents.weekly_meals import WeeklyMealsAgent
from mealprepper.config import get_settings
from mealprepper.models.plans import PlanStatus, WeeklyPlan
from mealprepper.orchestration.state import WorkflowPhase, WorkflowState
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


class MealPrepperSupervisor:
    """Simple supervisor wiring plan → approve → grocery → daily cycle."""

    def __init__(
        self,
        store: SQLiteStore | None = None,
        weekly_agent: WeeklyMealsAgent | None = None,
        grocery_agent: GroceryListAgent | None = None,
        comms_agent: CommunicationsAgent | None = None,
    ) -> None:
        self.store = store or SQLiteStore()
        self.weekly = weekly_agent or WeeklyMealsAgent(store=self.store)
        self.grocery = grocery_agent or GroceryListAgent(store=self.store)
        self.comms = comms_agent or CommunicationsAgent(store=self.store)
        self.settings = get_settings()
        self.state = WorkflowState()

    def plan_week(self, week_start: date | None = None, auto_approve: bool = False) -> WorkflowState:
        self.state.phase = WorkflowPhase.PLANNING
        result = self.weekly.plan_week(week_start=week_start)
        if not result.success:
            self.state.last_error = result.message
            self.state.record(result.message)
            return self.state

        plan: WeeklyPlan = result.data
        self.state.plan = plan
        self.state.week_start = plan.week_start
        self.state.record(result.message)

        if self.settings.approval_required and not auto_approve:
            self.state.phase = WorkflowPhase.AWAITING_APPROVAL
            approval = self.comms.request_approval(plan)
            self.state.approval_request_id = approval.data.get("request_id") if approval.data else None
            self.state.record(approval.message)
        else:
            self.store.update_plan_status(plan.id or "", PlanStatus.APPROVED)
            plan.status = PlanStatus.APPROVED
            self.state.plan = plan
            self.state.phase = WorkflowPhase.APPROVED
            self.state.record("Plan auto-approved")

        return self.state

    def approve_plan(self, plan_id: str | None = None) -> WorkflowState:
        plan = self._resolve_plan(plan_id)
        if not plan:
            self.state.last_error = "No plan to approve"
            return self.state
        self.store.update_plan_status(plan.id or "", PlanStatus.APPROVED)
        plan.status = PlanStatus.APPROVED
        self.state.plan = plan
        self.state.phase = WorkflowPhase.APPROVED
        self.state.record(f"Plan {plan.id} approved")
        return self.state

    def generate_grocery(self, plan_id: str | None = None) -> WorkflowState:
        self.state.phase = WorkflowPhase.GROCERY
        result = self.grocery.generate(plan_id=plan_id)
        if not result.success:
            self.state.last_error = result.message
            self.state.record(result.message)
            return self.state
        self.state.grocery = result.data["grocery"]
        self.state.phase = WorkflowPhase.ACTIVE
        self.state.record(result.message)
        return self.state

    def send_daily(self, target: date | None = None) -> WorkflowState:
        result = self.comms.send_daily_summary(target=target)
        self.state.record(result.message)
        if not result.success:
            self.state.last_error = result.message
        return self.state

    def process_feedback(self) -> WorkflowState:
        result = self.comms.process_pending_feedback()
        self.state.record(result.message)
        return self.state

    def handle_message(self, text: str) -> WorkflowState:
        result = self.comms.handle_inbound(text)
        self.state.record(result.message)
        if result.success and "approved" in result.message.lower():
            self.state.phase = WorkflowPhase.APPROVED
            if result.data and "plan_id" in result.data:
                self.state.plan = self.store.get_weekly_plan(result.data["plan_id"])
        return self.state

    def run_weekly_cycle(self, week_start: date | None = None) -> WorkflowState:
        """Saturday workflow: plan + request approval."""
        return self.plan_week(week_start=week_start)

    def run_grocery_cycle(self, plan_id: str | None = None) -> WorkflowState:
        """Sunday workflow: generate grocery from approved plan."""
        return self.generate_grocery(plan_id=plan_id)

    def _resolve_plan(self, plan_id: str | None):
        if plan_id:
            return self.store.get_weekly_plan(plan_id)
        return self.store.get_latest_plan(PlanStatus.PENDING_APPROVAL) or self.store.get_latest_plan()

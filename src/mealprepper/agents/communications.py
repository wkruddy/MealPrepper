from __future__ import annotations

import logging
from datetime import date

from mealprepper.agents.base import AgentResult, BaseAgent
from mealprepper.config import get_settings
from mealprepper.models.plans import PlanStatus, WeeklyPlan
from mealprepper.skills.comms_formatter import CommsFormatterSkill
from mealprepper.skills.feedback_collector import FeedbackCollectorSkill
from mealprepper.skills.playbook_renderer import PlaybookRendererSkill
from mealprepper.skills.preference_learner import PreferenceLearnerSkill
from mealprepper.skills.comms import CommsCommunicatorSkill
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)


COMMUNICATIONS_SYSTEM = """You are the Communications Agent for MealPrepper.

Your responsibilities:
- Send weekly plan approval requests via Slack/Discord/Telegram/iMessage
- Send daily morning meal summaries
- Parse feedback and approval replies
- Coordinate with preference learning

Available tools: send_approval, send_daily, parse_feedback, process_preferences, format_playbook"""


class CommunicationsAgent(BaseAgent):
    name = "communications"
    system_prompt = COMMUNICATIONS_SYSTEM

    def __init__(
        self,
        store: SQLiteStore | None = None,
        comms: CommsCommunicatorSkill | None = None,
        formatter: CommsFormatterSkill | None = None,
        feedback: FeedbackCollectorSkill | None = None,
        preferences: PreferenceLearnerSkill | None = None,
        playbook: PlaybookRendererSkill | None = None,
        **kwargs,
    ) -> None:
        self.store = store or SQLiteStore()
        self.comms = comms or CommsCommunicatorSkill()
        self.formatter = formatter or CommsFormatterSkill()
        self.feedback = feedback or FeedbackCollectorSkill()
        self.preferences = preferences or PreferenceLearnerSkill(self.store)
        self.playbook = playbook or PlaybookRendererSkill()
        self.settings = get_settings()
        super().__init__(**kwargs)

    def _register_tools(self) -> None:
        self.register_tool("send_approval", "Notify family that weekly plan needs approval", self._send_approval)
        self.register_tool("send_daily", "Send today's meal summary", self._send_daily)
        self.register_tool("parse_feedback", "Parse feedback or approval text", self._parse_feedback)
        self.register_tool(
            "process_preferences", "Apply pending feedback to preferences", self._process_prefs
        )
        self.register_tool("format_playbook", "Render plan playbook markdown", self._format_playbook)

    def request_approval(self, plan: WeeklyPlan) -> AgentResult:
        summary = self.run_tool("format_playbook", plan=plan, mode="approval")
        sent = self.run_tool("send_approval", plan=plan, summary=summary)
        request_id = self.store.create_approval_request(plan.id or "", summary)
        return AgentResult(
            success=sent,
            message="Approval request sent" if sent else "Failed to send approval",
            data={"request_id": request_id, "summary": summary},
        )

    def send_daily_summary(self, target: date | None = None) -> AgentResult:
        target = target or date.today()
        sent = self.run_tool("send_daily", target=target)
        plan = self.store.get_plan_for_date(target)
        meals_count = len(plan.meals_for_day(target.strftime("%A").lower())) if plan else 0
        return AgentResult(
            success=sent,
            message=f"Daily summary sent for {target.isoformat()} ({meals_count} meals)",
            data={"date": target.isoformat()},
        )

    def handle_inbound(self, text: str) -> AgentResult:
        approval = self.feedback.parse_approval(text)
        if approval is not None:
            pending = self.store.get_pending_approval()
            if pending and approval:
                plan_id = pending["weekly_plan_id"]
                self.store.update_plan_status(plan_id, PlanStatus.APPROVED)
                self.store.resolve_approval(pending["id"], True, text)
                return AgentResult(success=True, message="Plan approved", data={"plan_id": plan_id})
            if pending and not approval:
                self.store.resolve_approval(pending["id"], False, text)
                return AgentResult(success=True, message="Plan rejected — revise and re-plan")

        plan = self.store.get_plan_for_date(date.today())
        meal_ctx = self.feedback.suggest_meal_for_feedback(plan)
        fb = self.run_tool(
            "parse_feedback",
            text=text,
            meal_title=meal_ctx.meal_title,
            meal_block=meal_ctx.meal_block,
            day=meal_ctx.day,
        )
        if fb:
            saved = self.store.save_feedback(fb)
            self.run_tool("process_preferences")
            return AgentResult(
                success=True,
                message=self.formatter.format_feedback_ack(saved.meal_title, saved.rating.value),
                data=saved,
            )
        return AgentResult(success=False, message="Could not parse message")

    def process_pending_feedback(self) -> AgentResult:
        profile = self.run_tool("process_preferences")
        return AgentResult(success=True, message="Preferences updated", data=profile)

    def _send_approval(self, plan: WeeklyPlan, summary: str | None = None, **_) -> bool:
        body = summary or self.formatter.format_approval(plan)
        return self.comms.send_approval_request(body)

    def _send_daily(self, target: date | None = None, **_) -> bool:
        target = target or date.today()
        plan = self.store.get_plan_for_date(target)
        if not plan:
            latest = self.store.get_latest_plan(PlanStatus.APPROVED)
            if latest:
                body = (
                    f"No active plan for {target.isoformat()}. "
                    f"Latest approved plan covers {latest.week_start} — {latest.week_end}."
                )
            else:
                body = f"No active plan for {target.isoformat()}."
            return self.comms.send_daily_plan(body)
        summary = self.formatter.daily_summary_from_plan(plan, target)
        body = self.formatter.format_daily_summary(summary)
        return self.comms.send_daily_plan(body)

    def _parse_feedback(self, text: str, meal_title: str = "", **kwargs):
        return self.feedback.parse_message(text, meal_title=meal_title, **kwargs)

    def _process_prefs(self, **_) -> object:
        return self.preferences.process_unapplied()

    def _format_playbook(self, plan: WeeklyPlan, mode: str = "full", **_) -> str:
        if mode == "approval":
            return self.playbook.render_approval_summary(plan)
        return self.playbook.render_plan(plan)

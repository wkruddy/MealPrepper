from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date

from mealprepper.models.plans import PlanStatus
from mealprepper.models.recipe_repository import SavedRecipe
from mealprepper.orchestration.supervisor import MealPrepperSupervisor
from mealprepper.skills.comms.slack_format import (
    SlackMessageBuilder,
    build_daily_messages,
    build_week_recipes_messages,
    build_week_titles_messages,
    format_planned_meal_recipe,
    slack_message_payload,
)
from mealprepper.skills.comms_formatter import CommsFormatterSkill
from mealprepper.skills.playbook_renderer import PlaybookRendererSkill
from mealprepper.skills.recipe_repository import RecipeRepositorySkill

HELP_TEXT = """*MealPrepper commands*

*Approvals*
• `approve` — approve the pending weekly plan
• `reject` — reject the plan (re-plan needed)

*Planning*
• `plan-week` — generate a new weekly plan (_requires confirmation_)
• `confirm plan-week` — proceed after the warning
• `cancel` — cancel a pending confirmation

*Info*
• `status` — current plan status
• `plan` — this week's meal titles
• `plan-recipes` — full week with ingredients and steps
• `daily` — today's meals from the active approved plan
• `grocery` — build grocery list from approved plan

*Family recipe library*
• `recipes` — list saved family recipes
• `recipes chicken` — search the library
• `recipe salmon` — show steps for a saved or planned meal
• `add-recipe Mild turkey tacos — kids love avocado` — save a meal idea

*Feedback*
• `loved chicken tacos` / `liked` / `disliked` / `neutral`

*Usage*
• Type commands in this channel, mention @MealPrepper, or use `/mealprepper <command>`
"""

KNOWN_COMMANDS = frozenset(
    {
        "help",
        "commands",
        "?",
        "approve",
        "reject",
        "status",
        "plan",
        "plan-recipes",
        "plan-week",
        "confirm",
        "cancel",
        "daily",
        "grocery",
        "recipes",
        "list-recipes",
        "recipe",
        "add-recipe",
        "import-recipe",
    }
)

PENDING_TTL_SECONDS = 300


@dataclass
class BotReply:
    text: str
    success: bool = True
    blocks: list[dict] | None = None
    payloads: list[dict] | None = None
    defer: str | None = None


def strip_bot_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


def parse_command_text(text: str) -> tuple[str, str]:
    """Return (command, args) from user text or slash-command payload."""
    cleaned = strip_bot_mention(text).strip()
    if not cleaned:
        return "", ""

    if cleaned.startswith("/mealprepper"):
        cleaned = cleaned[len("/mealprepper") :].strip()
    elif cleaned.startswith("/mp"):
        cleaned = cleaned[len("/mp") :].strip()

    if not cleaned:
        return "help", ""

    parts = cleaned.split(maxsplit=1)
    command = parts[0].lower().lstrip("/")
    args = parts[1].strip() if len(parts) > 1 else ""
    return command, args


class MealPrepperBotHandler:
    """Dispatch inbound chat commands to MealPrepper supervisor workflows."""

    def __init__(
        self,
        supervisor: MealPrepperSupervisor | None = None,
        recipe_repo: RecipeRepositorySkill | None = None,
    ) -> None:
        self.supervisor = supervisor or MealPrepperSupervisor()
        self.store = self.supervisor.store
        self.playbook = PlaybookRendererSkill()
        self.formatter = CommsFormatterSkill()
        self.recipe_repo = recipe_repo or RecipeRepositorySkill(store=self.store)
        self._pending: dict[str, tuple[str, float]] = {}

    def handle(self, text: str, *, channel: str = "") -> BotReply:
        command, args = parse_command_text(text)
        if not command:
            return BotReply("Say `help` for commands.", success=False)

        if command in {"help", "commands", "?"}:
            return BotReply(HELP_TEXT)

        if command == "approve":
            return self._handle_approval(approve=True)

        if command == "reject":
            return self._handle_approval(approve=False)

        if command == "status":
            return self._handle_status()

        if command == "plan":
            return self._handle_plan()

        if command == "plan-recipes":
            return self._handle_plan_recipes()

        if command == "plan-week":
            return self._handle_plan_week(channel)

        if command == "confirm":
            return self._handle_confirm(args, channel)

        if command == "cancel":
            return self._handle_cancel(channel)

        if command == "daily":
            return self._handle_daily()

        if command == "grocery":
            return self._handle_grocery()

        if command in {"recipes", "list-recipes"}:
            return self._handle_recipes(args)

        if command == "recipe":
            return self._handle_recipe(args)

        if command in {"add-recipe", "import-recipe"}:
            return self._handle_add_recipe(args)

        message = f"{command} {args}".strip() if args else command
        if command not in KNOWN_COMMANDS:
            message = text.strip()

        state = self.supervisor.handle_message(message)
        if state.last_error:
            return BotReply(state.last_error, success=False)
        if not state.messages:
            return BotReply(
                "I didn't understand that. Try `help`, `approve`, `status`, or `loved <meal>`.",
                success=False,
            )
        return BotReply("\n".join(state.messages), success=True)

    def run_deferred(self, action: str) -> BotReply:
        if action == "plan-week":
            return self._execute_plan_week()
        return BotReply(f"Unknown deferred action: {action}", success=False)

    def _handle_approval(self, *, approve: bool) -> BotReply:
        pending = self.store.get_pending_approval()
        if not pending:
            return BotReply("No plan is waiting for approval.", success=False)

        state = self.supervisor.handle_message("APPROVE" if approve else "REJECT")
        if state.last_error:
            return BotReply(state.last_error, success=False)
        return BotReply(state.messages[-1] if state.messages else "Done.")

    def _handle_status(self) -> BotReply:
        pending = self.store.get_pending_approval()
        today = date.today()
        active = self.store.get_plan_for_date(today)
        plan = (
            active
            or self.store.get_latest_plan(PlanStatus.PENDING_APPROVAL)
            or self.store.get_latest_plan(PlanStatus.APPROVED)
            or self.store.get_latest_plan()
        )

        builder = SlackMessageBuilder()
        builder.header("MealPrepper status")
        if pending:
            builder.section(
                ":hourglass_flowing_sand: A weekly plan is *waiting for approval*. Reply `approve`."
            )
        else:
            builder.section(":white_check_mark: No pending approval.")

        if active:
            builder.divider()
            builder.section(
                f"*Active this week:* {active.week_start} — {active.week_end}\n"
                f"_{active.status.value}, {len(active.meals)} meals_"
            )
        elif plan:
            builder.divider()
            builder.section(
                f"*Latest plan:* {plan.week_start} — {plan.week_end}\n"
                f"_{plan.status.value}, {len(plan.meals)} meals_"
            )
            if plan.status in {PlanStatus.APPROVED, PlanStatus.ACTIVE} and today > plan.week_end:
                builder.context(
                    f"No plan covers today ({today.isoformat()}). "
                    "Run `plan-week` then `confirm plan-week` to plan this week."
                )
        else:
            builder.divider()
            builder.section("No weekly plan yet. Run `plan-week` to create one.")
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _resolve_plan_for_view(self):
        today = date.today()
        return (
            self.store.get_plan_for_date(today)
            or self.store.get_latest_plan(PlanStatus.PENDING_APPROVAL)
            or self.store.get_latest_plan(PlanStatus.APPROVED)
            or self.store.get_latest_plan()
        )

    def _stale_plan_note(self, plan) -> str:
        today = date.today()
        if plan.week_start <= today <= plan.week_end:
            return ""
        return (
            f"_Showing latest plan ({plan.week_start} — {plan.week_end}); "
            f"no plan covers today ({today.isoformat()})._"
        )

    def _handle_plan(self) -> BotReply:
        plan = self._resolve_plan_for_view()
        if not plan:
            return BotReply("No plan found. Run `plan-week` first.", success=False)
        payloads = build_week_titles_messages(plan, stale_note=self._stale_plan_note(plan))
        return BotReply(payloads[0]["text"], payloads=payloads)

    def _handle_plan_recipes(self) -> BotReply:
        plan = self._resolve_plan_for_view()
        if not plan:
            return BotReply("No plan found. Run `plan-week` first.", success=False)
        payloads = build_week_recipes_messages(plan, stale_note=self._stale_plan_note(plan))
        return BotReply(payloads[0]["text"], payloads=payloads)

    def _handle_plan_week(self, channel: str) -> BotReply:
        if not channel:
            return BotReply(
                "Run `plan-week` from a channel message (not supported without channel context).",
                success=False,
            )

        existing = self._resolve_plan_for_view()
        builder = SlackMessageBuilder()
        builder.header(":warning: Generate new weekly plan?")
        lines = [
            "This will run a *full replan* for the current week (several minutes).",
            "Any *pending approval* will be replaced. Approved plans for other weeks stay in history, "
            "but the new plan becomes what `daily`, `plan`, and `grocery` use going forward.",
            "",
            "*Use with discretion.*",
        ]
        if existing:
            lines.extend(
                [
                    "",
                    f"Current latest plan: *{existing.week_start} — {existing.week_end}* "
                    f"({existing.status.value}, {len(existing.meals)} meals)",
                ]
            )
        lines.append("")
        lines.append("Reply `confirm plan-week` within 5 minutes to proceed, or `cancel`.")
        builder.section("\n".join(lines))
        self._pending[channel] = ("plan-week", time.time() + PENDING_TTL_SECONDS)
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _handle_confirm(self, args: str, channel: str) -> BotReply:
        action = args.lower().strip() or "plan-week"
        if action != "plan-week":
            return BotReply("Usage: `confirm plan-week`", success=False)
        if not channel:
            return BotReply("Confirmation must be sent from the same channel.", success=False)

        pending = self._pending.get(channel)
        if not pending or pending[1] < time.time():
            self._pending.pop(channel, None)
            return BotReply(
                "No pending `plan-week` confirmation. Run `plan-week` first.",
                success=False,
            )
        if pending[0] != "plan-week":
            return BotReply(f"Unknown pending action `{pending[0]}`.", success=False)

        self._pending.pop(channel, None)
        return BotReply(
            ":hourglass_flowing_sand: Generating weekly plan — I'll post the result when ready "
            "(typically 2–5 minutes).",
            defer="plan-week",
        )

    def _handle_cancel(self, channel: str) -> BotReply:
        if channel and channel in self._pending:
            self._pending.pop(channel, None)
            return BotReply("Cancelled pending action.")
        return BotReply("Nothing to cancel.", success=False)

    def _execute_plan_week(self) -> BotReply:
        state = self.supervisor.plan_week()
        if state.last_error:
            return BotReply(f"Plan generation failed: {state.last_error}", success=False)

        plan = state.plan or self.store.get_latest_plan(PlanStatus.PENDING_APPROVAL)
        if not plan:
            return BotReply("Plan generation finished but no plan was saved.", success=False)

        builder = SlackMessageBuilder()
        builder.header(":white_check_mark: Weekly plan generated")
        builder.section(
            f"*Week {plan.week_start} — {plan.week_end}* · {len(plan.meals)} meals\n"
            f"Status: _{plan.status.value}_"
        )
        if plan.status == PlanStatus.PENDING_APPROVAL:
            builder.context("Reply `approve` to approve, or `plan` / `plan-recipes` to review.")
        payloads = build_week_titles_messages(plan)
        first = payloads[0]
        combined_blocks = builder.blocks + first.get("blocks", [])
        all_payloads = [{"text": first["text"], "blocks": combined_blocks}]
        all_payloads.extend(payloads[1:])
        return BotReply(all_payloads[0]["text"], success=True, payloads=all_payloads)

    def _handle_daily(self) -> BotReply:
        target = date.today()
        plan = self.store.get_plan_for_date(target)
        if not plan:
            latest = self.store.get_latest_plan(PlanStatus.APPROVED) or self.store.get_latest_plan()
            if latest and latest.status in {PlanStatus.APPROVED, PlanStatus.ACTIVE}:
                builder = SlackMessageBuilder()
                builder.header("No active plan for today")
                builder.section(
                    f"No meals for *{target.isoformat()}*.\n"
                    f"Latest approved plan: *{latest.week_start} — {latest.week_end}*."
                )
                builder.context("Run `plan-week` then `confirm plan-week` to plan this week.")
                payload = builder.to_payload()
                return BotReply(payload["text"], success=False, blocks=payload["blocks"])
            return BotReply(f"No active plan for {target.isoformat()}.", success=False)

        summary = self.formatter.daily_summary_from_plan(plan, target)
        meals = summary.meals
        if not meals:
            day_name = target.strftime("%A")
            return BotReply(
                f"No meals scheduled for *{day_name}* ({target.isoformat()}) in the active plan.",
                success=False,
            )

        meal_lines = []
        for meal in meals:
            block = meal.meal_block.replace("_", " ").title()
            prep = meal.recipe.prep_minutes + meal.recipe.cook_minutes
            meal_lines.append(f"• *{block}:* {meal.recipe.title}" + (f" _~{prep}m_" if prep else ""))

        extras = []
        if summary.prep_notes:
            extras.append(f"*Prep notes*\n{summary.prep_notes}")
        if summary.infant_blw_tips:
            extras.append(f"*Infant BLW*\n{summary.infant_blw_tips}")

        payloads = build_daily_messages(
            summary.day_name.title(),
            target.isoformat(),
            meal_lines,
            extras,
        )
        return BotReply(payloads[0]["text"], payloads=payloads)

    def _handle_grocery(self) -> BotReply:
        plan = self.store.get_latest_plan(PlanStatus.APPROVED) or self.store.get_latest_plan()
        if not plan:
            return BotReply("No plan found.", success=False)
        if plan.status not in {PlanStatus.APPROVED, PlanStatus.ACTIVE}:
            return BotReply(
                f"Plan is `{plan.status.value}` — approve it first with `approve`.",
                success=False,
            )

        state = self.supervisor.generate_grocery(plan_id=plan.id)
        if state.last_error:
            return BotReply(state.last_error, success=False)
        grocery = state.grocery
        if not grocery:
            return BotReply("Grocery list was not created.", success=False)

        items = grocery.must_buy or grocery.items
        builder = SlackMessageBuilder()
        builder.header(f"Grocery list — {grocery.week_label}")
        builder.context(f"{len(items)} items")
        builder.divider()
        lines = []
        for item in items[:20]:
            qty = f" — {item.quantity}" if item.quantity else ""
            lines.append(f"• {item.name}{qty}")
        builder.section("\n".join(lines))
        if len(items) > 20:
            builder.context(f"…and {len(items) - 20} more. Run `mealprepper show-grocery` on the server.")
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _handle_recipes(self, query: str) -> BotReply:
        limit = 20
        builder = SlackMessageBuilder()
        if query.strip():
            matches = self.recipe_repo.search(query.strip(), top_k=limit)
            if not matches:
                return BotReply(f"No family recipes match *{query}*.", success=False)
            builder.header(f"Recipes matching “{query}”")
            lines = []
            for match in matches:
                saved = self.store.get_saved_recipe(match.recipe_id)
                kind = "recipe" if saved and saved.has_full_recipe() else "idea"
                lines.append(f"• {match.title} _({kind})_")
            builder.section("\n".join(lines))
        else:
            saved = self.store.list_saved_recipes(limit=limit)
            if not saved:
                return BotReply(
                    "No family recipes saved yet. Try `add-recipe <idea>` or run `sync-recipes` on the server.",
                    success=False,
                )
            builder.header("Family recipe library")
            builder.context(f"{len(saved)} shown")
            builder.divider()
            lines = [f"• {item.title} _({'recipe' if item.has_full_recipe() else 'idea'})_" for item in saved]
            builder.section("\n".join(lines))
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _handle_recipe(self, query: str) -> BotReply:
        if not query.strip():
            return BotReply("Usage: `recipe <name>` — e.g. `recipe smash burger`", success=False)

        saved = self._find_saved_recipe(query)
        if saved:
            return self._recipe_reply_from_saved(saved)

        plan = self._resolve_plan_for_view()
        if plan:
            meal = self._find_planned_meal(plan, query)
            if meal:
                builder = SlackMessageBuilder()
                builder.header(f"{meal.day.title()} · {meal.recipe.title}")
                builder.section(format_planned_meal_recipe(meal))
                payload = builder.to_payload()
                return BotReply(payload["text"], blocks=payload["blocks"])

        return BotReply(f"No saved or planned recipe matches *{query}*.", success=False)

    def _handle_add_recipe(self, text: str) -> BotReply:
        if not text.strip():
            return BotReply("Usage: `add-recipe <meal idea or recipe text>`", success=False)
        saved = self.recipe_repo.import_text(
            text.strip(),
            source_label="Slack import",
            source_type="text",
        )
        kind = "full recipe" if saved.has_full_recipe() else "meal idea"
        builder = SlackMessageBuilder()
        builder.header("Recipe saved")
        builder.section(f"*{saved.title}* saved as a {kind} in the family library.")
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _recipe_reply_from_saved(self, saved: SavedRecipe) -> BotReply:
        builder = SlackMessageBuilder()
        kind = "recipe" if saved.has_full_recipe() else "idea"
        builder.header(saved.title)
        builder.context(f"{kind} · {saved.source_label or saved.source_type}")
        builder.divider()

        sections: list[str] = []
        if saved.notes:
            sections.append(saved.notes)
        recipe = saved.recipe
        if recipe and recipe.ingredients:
            lines = ["*Ingredients*"]
            for ingredient in recipe.ingredients:
                qty = " ".join(part for part in [ingredient.quantity, ingredient.unit] if part).strip()
                lines.append(f"• {ingredient.name}" + (f" — {qty}" if qty else ""))
            sections.append("\n".join(lines))
        if recipe and recipe.steps:
            lines = ["*Steps*"]
            for step in sorted(recipe.steps, key=lambda item: item.order):
                lines.append(f"{step.order}. {step.instruction}")
            sections.append("\n".join(lines))
        elif saved.key_ingredients:
            sections.append(f"*Key ingredients:* {', '.join(saved.key_ingredients)}")
        elif saved.raw_text and not recipe:
            sections.append(saved.raw_text[:1500])

        for index, section in enumerate(sections):
            builder.section(section)
            if index < len(sections) - 1:
                builder.divider()
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _find_saved_recipe(self, query: str) -> SavedRecipe | None:
        results = self.recipe_repo.search(query, top_k=5)
        if not results:
            return None
        top = results[0]
        return self.store.get_saved_recipe(top.recipe_id)

    def _find_planned_meal(self, plan, query: str):
        normalized = query.lower().strip()
        for meal in plan.meals:
            if normalized in meal.recipe.title.lower():
                return meal
        return None

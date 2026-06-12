from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date

from mealprepper.models.plans import PlanStatus, WeeklyPlan
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
from mealprepper.skills.pantry_config import _normalize_name
from mealprepper.skills.recipe_repository import RecipeRepositorySkill

from mealprepper.skills.recipe_matching import MIN_RECIPE_MATCH_SCORE, recipe_match_score
from mealprepper.services.family_admin import FamilyAdminService
from mealprepper.services.family_resolver import FamilyContext, WorkspacePendingOnboarding
from mealprepper.services.family_settings import FamilySettingsService
from mealprepper.skills.comms.profile_onboarding import (
    PROFILE_ONBOARDING_COMPLETE,
    PROFILE_ONBOARDING_DONE,
    ProfileOnboardingFlow,
    question_for_step,
)
from mealprepper.storage.sqlite import SQLiteStore

HELP_TEXT = """*MealPrepper commands*

*Approvals*
• `approve` — approve the pending weekly plan
• `reject` — reject the plan (re-plan needed)

*Planning*
• `plan-week` — generate a new weekly plan (_requires confirmation_)
• `confirm plan-week` — proceed after the warning
• `cancel` — cancel a pending confirmation

*Info*
• `household` — your household name and what's saved for you
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
• `remove-recipe Meatball Monday` — delete a saved recipe or idea

*Feedback*
• `loved chicken tacos` / `liked` / `disliked` / `neutral` / `reject`
• Feedback is saved and applied to future meal plans automatically

*Settings*
• `settings` — view family diet, members, pantry, schedule
• `settings members` / `settings diet` / `settings pantry` — focused views
• `remove keto` / `remove keto Alex` — drop a diet constraint
• `add keto Alex` — add a per-member diet constraint
• `set household diet gluten_free` — set household-wide diet
• `settings pantry add olive oil` / `settings pantry remove salt`
• `add member Sam adult 32` — add a household member

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
        "household",
        "whoami",
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
        "remove-recipe",
        "delete-recipe",
        "settings",
        "edit",
        "add",
        "remove",
        "set",
        "start",
        "onboard",
        "setup",
    }
)

PENDING_TTL_SECONDS = 300

ONBOARDING_PROMPT = (
    "Welcome to MealPrepper! Reply `start` to set up your household."
)

ONBOARDING_ASK_NAME = (
    "Let's set up your household!\n\n"
    "What would you like to call it? (e.g. *Alex's Family*)\n\n"
    "_Keep replying in this thread._"
)

ONBOARDING_CONFIRM = (
    "Create household *{name}*?\n\n"
    "Reply `confirm` to save it. (Nothing is stored until you confirm.)\n"
    "Reply `cancel` to start over."
)

ONBOARDING_COMPLETE = (
    "Your household *{name}* is ready!\n\n"
    "Next up: a few quick questions so meal plans fit you."
)

ONBOARDING_HELP = """*MealPrepper — getting started*

This Slack workspace has MealPrepper installed but you haven't set up a household yet.

• `start` — begin household setup
• `household` — see setup progress or your saved household
• `help` — show this message

Setup is saved only after you reply `confirm`. Keep replying in the same thread.
"""


@dataclass
class _OnboardingSession:
    step: str  # "name" | "confirm"
    household_name: str = ""
    thread_ts: str = ""


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
        settings_service: FamilySettingsService | None = None,
    ) -> None:
        self.supervisor = supervisor or MealPrepperSupervisor()
        self.store = self.supervisor.store
        self.playbook = PlaybookRendererSkill()
        self.formatter = CommsFormatterSkill()
        self.recipe_repo = recipe_repo or RecipeRepositorySkill(store=self.store)
        self.settings_service = settings_service or FamilySettingsService(
            db_path=self.store.db_path,
        )
        self._family_admin = FamilyAdminService(db_path=self.store.db_path)
        self._profile_onboarding = ProfileOnboardingFlow(self.settings_service)
        self._pending: dict[str, tuple[str, float]] = {}
        self._active_workspace_id = ""
        self._active_channel = ""
        self._active_slack_user_id = ""
        self._pending_onboarding = False
        self._onboarding_sessions: dict[str, _OnboardingSession] = {}

    def anchor_onboarding_thread(
        self,
        workspace_id: str,
        slack_user_id: str,
        thread_ts: str,
    ) -> None:
        """Set thread anchor after slash-command replies (no message ts on slash invocations)."""
        anchor = thread_ts.strip()
        if not workspace_id or not slack_user_id or not anchor:
            return
        key = f"{workspace_id}:{slack_user_id}"
        session = self._onboarding_sessions.get(key)
        if session and not session.thread_ts:
            session.thread_ts = anchor

        family_id = self._family_id_for_slack_user(workspace_id, slack_user_id)
        if not family_id:
            return
        step, data = self.settings_service.get_profile_onboarding(family_id)
        if step and step != PROFILE_ONBOARDING_COMPLETE and not data.get("thread_ts"):
            data["thread_ts"] = anchor
            self.settings_service.set_profile_onboarding_step(family_id, step, data)

    def _onboarding_key(self) -> str:
        return f"{self._active_workspace_id}:{self._active_slack_user_id}"

    def has_onboarding_session(self, workspace_id: str, slack_user_id: str) -> bool:
        if not workspace_id or not slack_user_id:
            return False
        return f"{workspace_id}:{slack_user_id}" in self._onboarding_sessions

    def get_onboarding_thread_ts(self, workspace_id: str, slack_user_id: str) -> str | None:
        session = self._onboarding_sessions.get(f"{workspace_id}:{slack_user_id}")
        if session and session.thread_ts:
            return session.thread_ts
        return self.get_profile_onboarding_thread_ts(workspace_id, slack_user_id)

    def should_accept_onboarding_message(self, workspace_id: str, slack_user_id: str) -> bool:
        """Accept free-text follow-ups while a user is mid household setup."""
        if self.has_onboarding_session(workspace_id, slack_user_id):
            return True
        return self.should_accept_profile_onboarding_message(workspace_id, slack_user_id)

    def should_accept_profile_onboarding_message(
        self,
        workspace_id: str,
        slack_user_id: str,
    ) -> bool:
        step = self._profile_onboarding_step(workspace_id, slack_user_id)
        return bool(step and step != PROFILE_ONBOARDING_COMPLETE)

    def get_profile_onboarding_thread_ts(
        self,
        workspace_id: str,
        slack_user_id: str,
    ) -> str | None:
        family_id = self._family_id_for_slack_user(workspace_id, slack_user_id)
        if not family_id:
            return None
        _, data = self.settings_service.get_profile_onboarding(family_id)
        thread_ts = str(data.get("thread_ts") or "").strip()
        return thread_ts or None

    def _family_id_for_slack_user(self, workspace_id: str, slack_user_id: str) -> str | None:
        if not workspace_id or not slack_user_id:
            return None
        try:
            ctx = self.settings_service.for_slack_workspace(
                workspace_id,
                slack_user_id=slack_user_id,
            )
        except (WorkspacePendingOnboarding, ValueError):
            return None
        return ctx.family_id

    def _profile_onboarding_step(self, workspace_id: str, slack_user_id: str) -> str | None:
        family_id = self._family_id_for_slack_user(workspace_id, slack_user_id)
        if not family_id:
            return None
        step, _ = self.settings_service.get_profile_onboarding(family_id)
        return step

    def _bind_slack_context(
        self,
        channel: str,
        workspace_id: str,
        slack_user_id: str = "",
    ) -> BotReply | None:
        workspace_id = (workspace_id or "").strip()
        slack_user_id = (slack_user_id or "").strip()
        self._active_channel = channel or ""
        self._active_workspace_id = workspace_id
        self._active_slack_user_id = slack_user_id
        self._pending_onboarding = False
        if not workspace_id:
            return None
        try:
            ctx = self.settings_service.for_slack_workspace(
                workspace_id,
                channel or None,
                slack_user_id or None,
            )
        except WorkspacePendingOnboarding:
            self._pending_onboarding = True
            return None
        except ValueError:
            return BotReply(
                "This Slack workspace isn't connected to MealPrepper yet.\n"
                "Ask the person who runs MealPrepper to install the app in this workspace.",
                success=False,
            )
        self._scope_to_family(ctx)
        return None

    def _refresh_family_scope(self) -> None:
        if not self._active_workspace_id:
            return
        ctx = self.settings_service.for_slack_workspace(
            self._active_workspace_id,
            self._active_channel or None,
            self._active_slack_user_id or None,
        )
        self._scope_to_family(ctx)

    def _handle_onboarding(
        self,
        command: str,
        args: str,
        text: str,
        *,
        message_ts: str = "",
        thread_ts: str = "",
    ) -> BotReply:
        key = self._onboarding_key()
        session = self._onboarding_sessions.get(key)

        if command in {"start", "onboard", "setup"}:
            anchor = (thread_ts or message_ts).strip()
            self._onboarding_sessions[key] = _OnboardingSession(step="name", thread_ts=anchor)
            return BotReply(ONBOARDING_ASK_NAME)

        if command in {"help", "commands", "?"}:
            return BotReply(ONBOARDING_HELP)

        if command in {"household", "whoami"}:
            return self._handle_household(session)

        if command in {"status", "settings", "recipes", "plan", "daily", "grocery", "plan-week"}:
            if session:
                return BotReply(
                    "Finish household setup first — reply with your household name, then `confirm`.",
                    success=False,
                )
            return BotReply(ONBOARDING_PROMPT, success=False)

        if command == "cancel":
            self._onboarding_sessions.pop(key, None)
            return BotReply("Household setup cancelled. Say `start` when you're ready.")

        if session and session.step == "confirm" and command == "confirm":
            return self._complete_onboarding(
                session.household_name,
                thread_ts=thread_ts or session.thread_ts,
            )

        if session and session.step == "name":
            name = args.strip() if command == "name" and args else text.strip()
            if not name or name.lower() in {"start", "onboard", "setup"}:
                return BotReply(
                    "Please send a household name (e.g. *Alex's Family*).",
                    success=False,
                )
            session.household_name = name
            session.step = "confirm"
            return BotReply(ONBOARDING_CONFIRM.format(name=name))

        if session and session.step == "confirm":
            if command == "confirm" or text.strip().lower() == "confirm":
                return self._complete_onboarding(
                    session.household_name,
                    thread_ts=thread_ts or session.thread_ts,
                )
            return BotReply(
                ONBOARDING_CONFIRM.format(name=session.household_name),
                success=False,
            )

        if not command:
            return BotReply(ONBOARDING_PROMPT, success=False)
        return BotReply(ONBOARDING_PROMPT, success=False)

    def _complete_onboarding(
        self,
        household_name: str,
        *,
        thread_ts: str = "",
    ) -> BotReply:
        key = self._onboarding_key()
        session = self._onboarding_sessions.get(key)
        anchor = (thread_ts or (session.thread_ts if session else "")).strip()
        try:
            detail = self._family_admin.create_household_for_slack_user(
                workspace_id=self._active_workspace_id,
                slack_user_id=self._active_slack_user_id,
                name=household_name,
            )
        except ValueError as exc:
            return BotReply(str(exc), success=False)

        self._onboarding_sessions.pop(key, None)
        self._pending_onboarding = False
        bind_error = self._bind_slack_context(
            self._active_channel,
            self._active_workspace_id,
            self._active_slack_user_id,
        )
        if bind_error:
            return bind_error

        intro = self._profile_onboarding.start(
            detail.id,
            household_name=detail.name,
            thread_ts=anchor,
        )
        return BotReply(
            f"{ONBOARDING_COMPLETE.format(name=detail.name)}\n\n{intro.text}",
            success=True,
        )

    def _handle_profile_onboarding(self, text: str) -> BotReply:
        family_id = self._family_id()
        step, data = self.settings_service.get_profile_onboarding(family_id)
        if not step or step == PROFILE_ONBOARDING_COMPLETE:
            return BotReply("Say `help` for commands.", success=False)

        command, _ = parse_command_text(text)
        if command in {"help", "commands", "?"}:
            return BotReply(
                "You're finishing setup.\n\n"
                f"{question_for_step(step)}\n\n"
                "Reply in this thread, or say `skip setup` to finish later.",
                success=False,
            )
        if command in {"household", "whoami"}:
            return BotReply(
                f"Finishing setup — current question:\n\n{question_for_step(step)}",
                success=False,
            )

        command, args = parse_command_text(text)
        if command in KNOWN_COMMANDS and command not in {
            "help",
            "commands",
            "?",
            "household",
            "whoami",
            "skip",
            "skip-setup",
            "skip_setup",
        }:
            return BotReply(
                "Still finishing setup — answer the question above, say `skip`, or `skip setup`.",
                success=False,
            )

        result = self._profile_onboarding.handle_answer(family_id, step, text, data)
        if result.text == PROFILE_ONBOARDING_DONE:
            self._refresh_family_scope()
        return BotReply(result.text, success=result.success)

    def _scope_to_family(self, ctx: FamilyContext) -> None:
        if self.store.family_id == ctx.family_id:
            return
        self.store = SQLiteStore(db_path=self.store.db_path, family_id=ctx.family_id)
        self.supervisor = MealPrepperSupervisor(store=self.store, family_context=ctx)
        self.recipe_repo = RecipeRepositorySkill(store=self.store)

    def handle(
        self,
        text: str,
        *,
        channel: str = "",
        workspace_id: str = "",
        slack_user_id: str = "",
        message_ts: str = "",
        thread_ts: str = "",
    ) -> BotReply:
        bind_error = self._bind_slack_context(channel, workspace_id, slack_user_id)
        if bind_error:
            return bind_error

        command, args = parse_command_text(text)
        if self._pending_onboarding:
            return self._handle_onboarding(
                command,
                args,
                text,
                message_ts=message_ts,
                thread_ts=thread_ts,
            )

        profile_step = self._profile_onboarding_step(workspace_id, slack_user_id)
        if profile_step and profile_step != PROFILE_ONBOARDING_COMPLETE:
            if command in {"status", "settings", "recipes", "plan", "daily", "grocery", "plan-week"}:
                return BotReply(
                    "Let's finish setup first — reply in this thread, or say `skip setup`.",
                    success=False,
                )
            return self._handle_profile_onboarding(text)

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

        if command in {"household", "whoami"}:
            return self._handle_household()

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

        if command in {"remove-recipe", "delete-recipe"}:
            return self._handle_remove_recipe(args)

        if command == "settings":
            return self._handle_settings(args, channel)

        if command == "edit":
            return self._handle_edit(args, channel)

        if command == "remove":
            return self._handle_remove_setting(args)

        if command == "add":
            return self._handle_add_setting(args)

        if command == "set":
            return self._handle_set_setting(args)

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
        reply_text = state.messages[-1]
        if command in {"loved", "liked", "disliked", "neutral", "reject"}:
            builder = SlackMessageBuilder()
            builder.section(f":thumbsup: {reply_text}")
            builder.context("This will inform next week's meal plan.")
            payload = builder.to_payload()
            return BotReply(payload["text"], success=True, blocks=payload["blocks"])
        return BotReply(reply_text, success=True)

    def run_deferred(
        self,
        action: str,
        *,
        workspace_id: str = "",
        channel: str = "",
        slack_user_id: str = "",
    ) -> BotReply:
        if workspace_id or channel or slack_user_id:
            bind_error = self._bind_slack_context(channel, workspace_id, slack_user_id)
            if bind_error:
                return bind_error
        if action == "plan-week":
            return self._execute_plan_week()
        if action == "grocery":
            return self._execute_grocery()
        return BotReply(f"Unknown deferred action: {action}", success=False)

    def _handle_settings(self, args: str, channel: str) -> BotReply:
        summary = self.settings_service.get_summary(self.supervisor.family_context.family_id)
        section = args.lower().strip()

        builder = SlackMessageBuilder()
        builder.header("Family settings")

        if section in {"", "all"}:
            builder.section(self.settings_service.format_slack_summary(summary))
        elif section == "members":
            lines = []
            for m in summary.members:
                constraints = m.get("constraints") or {}
                diet = [k for k, v in constraints.items() if v is True]
                lines.append(f"• *{m['name']}* ({m['role']}): {', '.join(diet) or 'no constraints'}")
            builder.section("*Members*\n" + "\n".join(lines))
        elif section in {"diet", "dietary"}:
            household = summary.dietary_household or ["_(none)_"]
            builder.section(f"*Household diet:* {', '.join(household)}")
            per_member = []
            for m in summary.members:
                c = m.get("constraints") or {}
                diet_keys = {
                    "keto", "vegetarian", "vegan", "gluten_free", "dairy_free", "no_spicy",
                }
                diets = [
                    k for k, v in c.items()
                    if v is True and ("diet" in k or k in diet_keys)
                ]
                if diets:
                    per_member.append(f"• {m['name']}: {', '.join(diets)}")
            if per_member:
                builder.section("*Per-member*\n" + "\n".join(per_member))
        elif section == "pantry" or section.startswith("pantry "):
            pantry_args = section[6:].strip() if section.startswith("pantry ") else ""
            if pantry_args:
                return self._handle_pantry_edit(pantry_args)
            builder.section(
                f"*Pantry:* {summary.pantry_on_hand_count} on-hand, "
                f"{summary.pantry_staples_count} weekly staples\n"
                "_Edit with `settings pantry add olive oil` or `settings pantry remove salt`_"
            )
        elif section in {"cuisines", "cuisine"}:
            cuisines = summary.cuisine_preferences or ["_(none set)_"]
            builder.section(f"*Cuisine preferences:* {', '.join(cuisines)}")
        elif section == "schedule":
            sched = summary.schedule or {}
            lines = [f"• {k}: {v}" for k, v in sched.items()] or ["_(none set)_"]
            builder.section("*Schedule*\n" + "\n".join(lines))
        elif section == "macros":
            mt = summary.macro_tracking
            if mt.enabled:
                builder.section(
                    f"*Macro tracking:* enabled\n"
                    f"Defaults: protein {mt.default_protein_g or '?'}g, "
                    f"calories {mt.default_calories or '?'}"
                )
            else:
                builder.section("*Macro tracking:* off\nSay `track macros` to opt in.")
        else:
            builder.section(
                f"Unknown section `{section}`. Try: `settings`, `settings members`, "
                "`settings diet`, `settings pantry`, `settings macros`"
            )

        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _handle_edit(self, args: str, channel: str) -> BotReply:
        if not args.strip():
            return BotReply(
                "Usage: `remove keto`, `add keto Alex`, `set household diet gluten_free`, "
                "`settings pantry add olive oil`, `add member Sam adult 32`",
                success=False,
            )
        lowered = args.lower().strip()
        if lowered.startswith("pantry "):
            return self._handle_pantry_edit(args[6:].strip())
        if lowered.startswith("member "):
            return self._handle_add_member(args[7:].strip())
        if lowered.startswith("household diet"):
            return self._handle_set_setting(f"household diet {args[14:].strip()}")
        return BotReply(
            "Try `remove keto`, `add keto Alex`, `set household diet <diet>`, "
            "or `settings pantry add <item>`.",
            success=False,
        )

    def _settings_change_reply(self, title: str, detail: str) -> BotReply:
        self._refresh_family_scope()
        builder = SlackMessageBuilder()
        builder.header(title)
        builder.section(detail)
        builder.context("Changes apply to the next `plan-week`.")
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _family_id(self) -> str:
        return self.supervisor.family_context.family_id

    def _handle_remove_setting(self, args: str) -> BotReply:
        parts = args.strip().split(maxsplit=1)
        if not parts:
            return BotReply("Usage: `remove keto` or `remove keto Alex`", success=False)
        constraint = parts[0]
        member_name = parts[1] if len(parts) > 1 else None
        try:
            removed_from = self.settings_service.remove_member_constraint(
                self._family_id(),
                constraint,
                member_name,
            )
        except ValueError as exc:
            return BotReply(str(exc), success=False)
        if member_name:
            detail = f"Removed *{constraint}* from *{member_name}*."
        else:
            names = ", ".join(removed_from)
            detail = f"Removed *{constraint}* from: {names}."
        return self._settings_change_reply("Diet constraint removed", detail)

    def _handle_add_setting(self, args: str) -> BotReply:
        lowered = args.lower().strip()
        if lowered.startswith("member "):
            return self._handle_add_member(args[7:].strip())

        parts = args.strip().split(maxsplit=1)
        if len(parts) < 2:
            return BotReply(
                "Usage: `add keto Alex` or `add member Sam adult 32`",
                success=False,
            )
        constraint, member_name = parts[0], parts[1]
        try:
            key = self.settings_service.add_member_constraint(
                self._family_id(),
                constraint,
                member_name,
            )
        except ValueError as exc:
            return BotReply(str(exc), success=False)
        return self._settings_change_reply(
            "Diet constraint added",
            f"Added *{key}* for *{member_name}*.",
        )

    def _handle_set_setting(self, args: str) -> BotReply:
        lowered = args.lower().strip()
        if not lowered.startswith("household diet"):
            return BotReply(
                "Usage: `set household diet gluten_free` (multiple diets allowed)",
                success=False,
            )
        diets_raw = args.strip()[len("household diet") :].strip()
        if not diets_raw:
            return BotReply("Usage: `set household diet <diet>`", success=False)
        diets = diets_raw.split()
        try:
            normalized = self.settings_service.set_household_diet(self._family_id(), diets)
        except ValueError as exc:
            return BotReply(str(exc), success=False)
        return self._settings_change_reply(
            "Household diet updated",
            f"Household diet is now: *{', '.join(normalized)}*.",
        )

    def _handle_pantry_edit(self, args: str) -> BotReply:
        parts = args.strip().split(maxsplit=1)
        if len(parts) < 2:
            return BotReply(
                "Usage: `settings pantry add olive oil` or `settings pantry remove salt`",
                success=False,
            )
        action, item = parts[0].lower(), parts[1].strip()
        family_id = self._family_id()
        try:
            if action == "add":
                name = self.settings_service.add_pantry_item(family_id, item)
                detail = f"Added *{name}* to on-hand pantry."
            elif action == "remove":
                name = self.settings_service.remove_pantry_item(family_id, item)
                detail = f"Removed *{name}* from pantry."
            else:
                return BotReply(
                    "Usage: `settings pantry add <item>` or `settings pantry remove <item>`",
                    success=False,
                )
        except ValueError as exc:
            return BotReply(str(exc), success=False)
        return self._settings_change_reply("Pantry updated", detail)

    def _handle_add_member(self, args: str) -> BotReply:
        parts = args.strip().split()
        if len(parts) < 3:
            return BotReply(
                "Usage: `add member <name> <role> <age>` — e.g. `add member Sam adult 32`",
                success=False,
            )
        name, role = parts[0], parts[1]
        age_raw = parts[2]
        try:
            age = float(age_raw)
        except ValueError:
            return BotReply(f"Age must be a number, got `{age_raw}`.", success=False)

        age_years = age if age >= 1 else None
        age_months = age * 12 if age < 1 else None
        try:
            member_id = self.settings_service.add_member(
                self._family_id(),
                name=name,
                role=role,
                age_years=age_years,
                age_months=age_months,
            )
        except ValueError as exc:
            return BotReply(str(exc), success=False)
        age_label = f"{age:g}y" if age_years is not None else f"{age * 12:g}mo"
        return self._settings_change_reply(
            "Member added",
            f"Added *{name}* ({role}, {age_label}) — id `{member_id}`.",
        )

    def _handle_approval(self, *, approve: bool) -> BotReply:
        pending = self.store.get_pending_approval()
        if not pending:
            return BotReply("No plan is waiting for approval.", success=False)

        state = self.supervisor.handle_message("APPROVE" if approve else "REJECT")
        if state.last_error:
            return BotReply(state.last_error, success=False)
        return BotReply(state.messages[-1] if state.messages else "Done.")

    def _handle_household(self, session: _OnboardingSession | None = None) -> BotReply:
        if self._pending_onboarding:
            return self._handle_household_onboarding(session)

        ctx = self.supervisor.family_context
        try:
            detail = self._family_admin.get_family_detail(ctx.family_id)
        except ValueError as exc:
            return BotReply(str(exc), success=False)

        builder = SlackMessageBuilder()
        builder.header("Your household")
        builder.section(f"*{detail.name}* (`{detail.slug}`)")
        builder.section(
            f"{detail.member_count} member(s) · {detail.recipe_count} saved recipe(s) · "
            f"{detail.plan_count} weekly plan(s)"
        )
        if detail.slack_users:
            builder.context("Linked to your Slack account — only you see this household's data.")
        elif ctx.slack and ctx.slack.workspace_id:
            builder.context(
                f"Shared workspace household in {ctx.slack.workspace_id} — "
                "everyone in this binding uses the same plan and recipes."
            )
        builder.section(
            "_Commands like `settings`, `status`, `recipes`, and `plan-week` apply to this household._"
        )
        payload = builder.to_payload()
        return BotReply(payload["text"], blocks=payload["blocks"])

    def _handle_household_onboarding(self, session: _OnboardingSession | None) -> BotReply:
        saved = None
        try:
            saved = self._family_admin.get_slack_user_household(
                self._active_workspace_id,
                self._active_slack_user_id,
            )
        except (sqlite3.Error, ValueError, TypeError):
            saved = None
        if saved:
            bind_error = self._bind_slack_context(
                self._active_channel,
                self._active_workspace_id,
                self._active_slack_user_id,
            )
            if not bind_error:
                return self._handle_household()

        if session and session.step == "name":
            return BotReply(
                "Household setup in progress.\n\n"
                "Send your household name (e.g. *Thom's House*).",
                success=False,
            )
        if session and session.step == "confirm":
            return BotReply(
                f"Almost done — create *{session.household_name}*?\n\n"
                "Reply `confirm` to save it. Nothing is stored until you confirm.",
                success=False,
            )
        return BotReply(
            "You don't have a household yet.\n\nReply `start` to set one up.",
            success=False,
        )

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
        error = self._grocery_precheck()
        if error:
            return error
        return BotReply(
            ":hourglass_flowing_sand: Building grocery list — I'll post it when ready.",
            defer="grocery",
        )

    def _grocery_precheck(self) -> BotReply | None:
        plan = self.store.get_latest_plan(PlanStatus.APPROVED) or self.store.get_latest_plan()
        if not plan:
            return BotReply("No plan found.", success=False)
        if plan.status not in {PlanStatus.APPROVED, PlanStatus.ACTIVE}:
            return BotReply(
                f"Plan is `{plan.status.value}` — approve it first with `approve`.",
                success=False,
            )
        return None

    def _execute_grocery(self) -> BotReply:
        plan = self.store.get_latest_plan(PlanStatus.APPROVED) or self.store.get_latest_plan()
        if not plan:
            return BotReply("No plan found.", success=False)

        state = self.supervisor.generate_grocery(plan_id=plan.id)
        if state.last_error:
            return BotReply(state.last_error, success=False)
        grocery = state.grocery
        if not grocery:
            return BotReply("Grocery list was not created.", success=False)

        from mealprepper.skills.comms.slack_format import build_grocery_messages

        payloads = build_grocery_messages(grocery)
        return BotReply(payloads[0]["text"], payloads=payloads)

    def _handle_recipes(self, query: str) -> BotReply:
        from mealprepper.skills.comms.slack_format import build_recipe_list_messages

        if query.strip():
            matches = self.recipe_repo.search(query.strip(), top_k=0)
            if not matches:
                return BotReply(f"No family recipes match *{query}*.", success=False)
            lines = []
            for match in matches:
                saved = self.store.get_saved_recipe(match.recipe_id)
                kind = "recipe" if saved and saved.has_full_recipe() else "idea"
                lines.append(f"• {match.title} _({kind})_")
            payloads = build_recipe_list_messages(
                f"Recipes matching “{query.strip()}”",
                lines,
                context=f"{len(lines)} match{'es' if len(lines) != 1 else ''}",
            )
        else:
            saved = self.store.list_saved_recipes(limit=0)
            if not saved:
                return BotReply(
                    "No family recipes saved yet. Try `add-recipe <idea>` or run `sync-recipes` on the server.",
                    success=False,
                )
            lines = [
                f"• {item.title} _({'recipe' if item.has_full_recipe() else 'idea'})_"
                for item in saved
            ]
            payloads = build_recipe_list_messages(
                "Family recipe library",
                lines,
                context=f"{len(saved)} saved",
            )
        return BotReply(payloads[0]["text"], payloads=payloads)

    def _handle_recipe(self, query: str) -> BotReply:
        if not query.strip():
            return BotReply("Usage: `recipe <name>` — e.g. `recipe smash burger`", success=False)

        match = self._find_best_recipe_match(query.strip())
        if not match:
            return BotReply(f"No saved or planned recipe matches *{query}*.", success=False)

        kind, payload_obj, plan = match
        if kind == "planned":
            meal = self._resolve_planned_meal_recipe(payload_obj, plan)
            builder = SlackMessageBuilder()
            builder.header(f"{meal.day.title()} · {meal.recipe.title}")
            context = "_From this week's meal plan_"
            if meal.cook_note:
                context = f"{context} · {meal.cook_note}"
            builder.context(context)
            builder.divider()
            builder.section(format_planned_meal_recipe(meal))
            payload = builder.to_payload()
            return BotReply(payload["text"], blocks=payload["blocks"])

        return self._recipe_reply_from_saved(payload_obj)

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

    def _handle_remove_recipe(self, query: str) -> BotReply:
        if not query.strip():
            return BotReply("Usage: `remove-recipe <title>` — e.g. `remove-recipe Meatball Monday`", success=False)
        try:
            removed = self.recipe_repo.remove_recipe(query.strip())
        except ValueError as exc:
            return BotReply(str(exc), success=False)
        kind = "recipe" if removed.has_full_recipe() else "idea"
        builder = SlackMessageBuilder()
        builder.header("Recipe removed")
        builder.section(f"Removed *{removed.title}* ({kind}) from the family library.")
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

    def _find_best_recipe_match(
        self,
        query: str,
    ) -> tuple[str, object, WeeklyPlan | None] | None:
        """Return (kind, meal_or_saved, plan) for the strongest query match."""
        candidates: list[tuple[int, str, object, WeeklyPlan | None]] = []

        plan = self.store.get_plan_for_date(date.today()) or self._resolve_plan_for_view()
        if plan:
            for meal in plan.meals:
                score = recipe_match_score(query, meal.recipe.title)
                if score >= MIN_RECIPE_MATCH_SCORE:
                    candidates.append((score, "planned", meal, plan))

        for result in self.recipe_repo.search(query, top_k=8):
            score = recipe_match_score(query, result.title)
            if score >= MIN_RECIPE_MATCH_SCORE:
                saved = self.store.get_saved_recipe(result.recipe_id)
                if saved:
                    candidates.append((score, "saved", saved, None))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, kind, payload, plan_ref = candidates[0]
        return kind, payload, plan_ref

    def _resolve_planned_meal_recipe(self, meal, plan):
        """Align planned meal recipe body with its title when possible."""
        normalized_title = _normalize_name(meal.recipe.title)
        saved_matches = self.recipe_repo.find_recipes_by_query(meal.recipe.title)
        for saved in saved_matches:
            if saved.has_full_recipe() and _normalize_name(saved.title) == normalized_title:
                resolved = meal.model_copy(deep=True)
                resolved.recipe = saved.to_meal_recipe(meal.recipe.title)
                return resolved

        if meal.cook_source_day and meal.cook_source_block and plan:
            source = next(
                (
                    candidate
                    for candidate in plan.meals
                    if candidate.day == meal.cook_source_day
                    and candidate.meal_block == meal.cook_source_block
                ),
                None,
            )
            if source and _normalize_name(source.recipe.title) == normalized_title:
                resolved = meal.model_copy(deep=True)
                resolved.recipe = source.recipe.model_copy(deep=True)
                return resolved

        return meal

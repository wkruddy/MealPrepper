from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mealprepper.skills.pantry_config import PantryConfig

from mealprepper.config import Settings, get_settings
from mealprepper.models.family import FamilyProfile
from mealprepper.storage.migrations import DEFAULT_FAMILY_ID

logger = logging.getLogger(__name__)


class WorkspacePendingOnboarding(Exception):
    """Slack workspace is installed but no household (family) is linked yet."""

    def __init__(self, binding: "SlackBinding") -> None:
        self.binding = binding
        super().__init__(f"Workspace {binding.workspace_id} pending household onboarding")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


@dataclass
class PlanningConfig:
    week_starts_on: str = "monday"
    minimize_waste: bool = True
    overlap_ingredients: bool = True
    dish_lookback_weeks: int = 2
    max_meal_repeat_days: int = 3
    cook_efficiency: dict[str, Any] = field(default_factory=dict)
    ingredient_cohesion: dict[str, Any] = field(default_factory=dict)
    cuisine_preferences: list[str] = field(default_factory=list)
    dietary_household: list[str] = field(default_factory=list)
    staple_patterns: list[dict[str, Any]] = field(default_factory=list)
    nutrition_goal: str = ""
    food_likes: str = ""
    foods_avoid: str = ""

    @classmethod
    def from_json(
        cls,
        planning_json: dict[str, Any],
        *,
        cuisine_preferences: list[str] | None = None,
        dietary_household: list[str] | None = None,
        staple_patterns: list[dict[str, Any]] | None = None,
    ) -> PlanningConfig:
        return cls(
            week_starts_on=planning_json.get("week_starts_on", "monday"),
            minimize_waste=bool(planning_json.get("minimize_waste", True)),
            overlap_ingredients=bool(planning_json.get("overlap_ingredients", True)),
            dish_lookback_weeks=int(planning_json.get("dish_lookback_weeks", 2)),
            max_meal_repeat_days=int(planning_json.get("max_meal_repeat_days", 3)),
            cook_efficiency=dict(planning_json.get("cook_efficiency", {})),
            ingredient_cohesion=dict(planning_json.get("ingredient_cohesion", {})),
            cuisine_preferences=list(cuisine_preferences or []),
            dietary_household=list(dietary_household or []),
            staple_patterns=list(staple_patterns or []),
            nutrition_goal=str(planning_json.get("nutrition_goal", "") or ""),
            food_likes=str(planning_json.get("food_likes", "") or ""),
            foods_avoid=str(planning_json.get("foods_avoid", "") or ""),
        )


@dataclass
class SlackBinding:
    id: str
    family_id: str
    workspace_id: str
    channel_id: str
    webhook_url: str = ""
    bot_token: str = ""


@dataclass
class FamilyContext:
    family_id: str
    profile: FamilyProfile
    pantry: PantryConfig
    planning: PlanningConfig
    slack: SlackBinding | None = None


class FamilyResolver:
    """Resolve FamilyContext from family id, slug, or Slack workspace/channel."""

    def __init__(
        self,
        db_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db_path = db_path or self.settings.database_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def default(self) -> FamilyContext:
        return self.for_family_id(DEFAULT_FAMILY_ID)

    def for_family_id(self, family_id: str) -> FamilyContext:
        conn = self._connect()
        try:
            family = conn.execute(
                "SELECT * FROM families WHERE id = ?",
                (family_id,),
            ).fetchone()
            if not family:
                if family_id == DEFAULT_FAMILY_ID:
                    return self._from_yaml_fallback()
                raise ValueError(f"Family not found: {family_id}")

            settings_row = conn.execute(
                "SELECT * FROM family_settings WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            members = conn.execute(
                """
                SELECT id, display_name, role, age_years, age_months, constraints_json, notes
                FROM family_members
                WHERE family_id = ?
                ORDER BY sort_order, display_name
                """,
                (family_id,),
            ).fetchall()
        finally:
            conn.close()

        meal_blocks = json.loads(settings_row["meal_blocks_json"]) if settings_row else []
        schedule = json.loads(settings_row["schedule_json"]) if settings_row else {}
        planning_json = json.loads(settings_row["planning_json"]) if settings_row else {}
        cuisine = json.loads(settings_row["cuisine_preferences_json"]) if settings_row else []
        dietary = json.loads(settings_row["dietary_household_json"]) if settings_row else []
        staples = json.loads(settings_row["staple_patterns_json"]) if settings_row else []

        profile = FamilyProfile.from_db(
            timezone=family["timezone"],
            members=[dict(m) for m in members],
            meal_blocks=meal_blocks,
            schedule=schedule,
        )
        pantry = self._pantry_for_family(family_id)
        planning = PlanningConfig.from_json(
            planning_json,
            cuisine_preferences=cuisine,
            dietary_household=dietary,
            staple_patterns=staples,
        )
        return FamilyContext(
            family_id=family_id,
            profile=profile,
            pantry=pantry,
            planning=planning,
        )

    def for_family_slug(self, slug: str) -> FamilyContext:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM families WHERE slug = ?",
                (slug,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            raise ValueError(f"Family not found for slug: {slug}")
        return self.for_family_id(row["id"])

    def resolve_slack(
        self,
        *,
        workspace_id: str | None = None,
        channel_id: str | None = None,
        slack_user_id: str | None = None,
        allow_default: bool = True,
    ) -> FamilyContext:
        """Resolve family from Slack workspace (team_id), channel, and optional user.

        Workspace binding is looked up first. Legacy bindings (family_id set) apply to
        all users in the workspace. Workspace-only installs (family_id NULL) resolve
        per-user via family_slack_users; missing user mapping raises WorkspacePendingOnboarding.
        """
        workspace_id = (workspace_id or "").strip()
        channel_id = (channel_id or "").strip()
        slack_user_id = (slack_user_id or "").strip()
        if not workspace_id and not channel_id:
            return self.default()

        conn = self._connect()
        try:
            binding = None
            if workspace_id:
                binding = conn.execute(
                    "SELECT * FROM slack_bindings WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchone()
            if not binding and channel_id:
                binding = conn.execute(
                    "SELECT * FROM slack_bindings WHERE channel_id = ?",
                    (channel_id,),
                ).fetchone()
        finally:
            conn.close()

        if binding:
            family_id = (binding["family_id"] or "").strip()
            if family_id:
                return self._context_from_binding(binding)

            if slack_user_id and workspace_id:
                user_family_id = self._family_id_for_slack_user(workspace_id, slack_user_id)
                if user_family_id:
                    ctx = self.for_family_id(user_family_id)
                    keys = set(binding.keys())
                    ctx.slack = SlackBinding(
                        id=binding["id"],
                        family_id=user_family_id,
                        workspace_id=binding["workspace_id"],
                        channel_id=binding["channel_id"] or "",
                        webhook_url=binding["webhook_url"] or "",
                        bot_token=(binding["bot_token"] or "") if "bot_token" in keys else "",
                    )
                    return ctx

            keys = set(binding.keys())
            pending = SlackBinding(
                id=binding["id"],
                family_id="",
                workspace_id=binding["workspace_id"],
                channel_id=binding["channel_id"] or "",
                webhook_url=binding["webhook_url"] or "",
                bot_token=(binding["bot_token"] or "") if "bot_token" in keys else "",
            )
            raise WorkspacePendingOnboarding(pending)

        if workspace_id and not allow_default:
            raise ValueError(f"Slack workspace not registered: {workspace_id}")

        logger.debug(
            "No slack binding for workspace=%s channel=%s; using default family",
            workspace_id or "(none)",
            channel_id or "(none)",
        )
        return self.default()

    def _family_id_for_slack_user(self, workspace_id: str, slack_user_id: str) -> str | None:
        conn = self._connect()
        try:
            if not _table_exists(conn, "family_slack_users"):
                return None
            row = conn.execute(
                """
                SELECT family_id FROM family_slack_users
                WHERE workspace_id = ? AND slack_user_id = ?
                """,
                (workspace_id, slack_user_id),
            ).fetchone()
        finally:
            conn.close()
        if row and row["family_id"]:
            return str(row["family_id"]).strip()
        return None

    def for_slack_workspace(
        self,
        workspace_id: str | None,
        channel_id: str | None = None,
        slack_user_id: str | None = None,
    ) -> FamilyContext:
        """Resolve family for an inbound Slack event (workspace-first, then per-user)."""
        return self.resolve_slack(
            workspace_id=workspace_id,
            channel_id=channel_id,
            slack_user_id=slack_user_id,
            allow_default=not bool((workspace_id or "").strip()),
        )

    def for_slack_channel(self, channel_id: str | None) -> FamilyContext:
        return self.resolve_slack(channel_id=channel_id, allow_default=True)

    def bot_token_for_workspace(self, workspace_id: str | None) -> str:
        """Return per-workspace bot token from slack_bindings, or empty string."""
        workspace_id = (workspace_id or "").strip()
        if not workspace_id:
            return ""
        conn = self._connect()
        try:
            if not _table_exists(conn, "slack_bindings"):
                return ""
            if not _column_exists(conn, "slack_bindings", "bot_token"):
                return ""
            row = conn.execute(
                "SELECT bot_token FROM slack_bindings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        finally:
            conn.close()
        if row and row["bot_token"]:
            return str(row["bot_token"]).strip()
        return ""

    def has_workspace_binding(self, workspace_id: str | None) -> bool:
        """True when OAuth or CLI registered this Slack workspace (with or without a family)."""
        workspace_id = (workspace_id or "").strip()
        if not workspace_id:
            return False
        conn = self._connect()
        try:
            if not _table_exists(conn, "slack_bindings"):
                return False
            row = conn.execute(
                "SELECT 1 FROM slack_bindings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def _context_from_binding(self, binding: sqlite3.Row) -> FamilyContext:
        ctx = self.for_family_id(binding["family_id"])
        keys = set(binding.keys())
        ctx.slack = SlackBinding(
            id=binding["id"],
            family_id=binding["family_id"],
            workspace_id=binding["workspace_id"],
            channel_id=binding["channel_id"],
            webhook_url=binding["webhook_url"] or "",
            bot_token=(binding["bot_token"] or "") if "bot_token" in keys else "",
        )
        return ctx

    def _pantry_for_family(self, family_id: str) -> "PantryConfig":
        from mealprepper.skills.pantry_config import PantryConfig, _normalize_name

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT category, item_name FROM family_pantry WHERE family_id = ?",
                (family_id,),
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            if family_id == DEFAULT_FAMILY_ID:
                return PantryConfig.from_settings(self.settings)
            return PantryConfig()
        on_hand: set[str] = set()
        weekly_staples: set[str] = set()
        for row in rows:
            name = _normalize_name(row["item_name"])
            if row["category"] == "weekly_staple":
                weekly_staples.add(name)
            elif row["category"].startswith("on_hand"):
                on_hand.add(name)
        return PantryConfig(on_hand=on_hand, weekly_staples=weekly_staples)

    def _from_yaml_fallback(self) -> FamilyContext:
        """Fallback when DB tables exist but default family not yet seeded."""
        from mealprepper.skills.pantry_config import PantryConfig

        merged = self.settings.merged_config()
        defaults = self.settings.load_yaml("default.yaml")
        profile = FamilyProfile.from_config(merged)
        planning = PlanningConfig.from_json(defaults.get("planning", {}))
        return FamilyContext(
            family_id=DEFAULT_FAMILY_ID,
            profile=profile,
            pantry=PantryConfig.from_settings(self.settings),
            planning=planning,
        )

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mealprepper.config import Settings, get_settings
from mealprepper.models.family import MemberRole
from mealprepper.models.settings import MacroGoals, MacroTrackingConfig
from mealprepper.services.family_resolver import FamilyContext, FamilyResolver
from mealprepper.skills.pantry_config import _normalize_name

DIET_CONSTRAINT_KEYS = frozenset(
    {
        "keto",
        "vegetarian",
        "vegan",
        "gluten_free",
        "dairy_free",
        "no_spicy",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SettingsSummary:
    """Read-only snapshot for Slack `settings` display."""

    family_id: str
    timezone: str
    members: list[dict[str, Any]]
    dietary_household: list[str]
    cuisine_preferences: list[str]
    staple_patterns: list[dict[str, Any]]
    schedule: dict[str, str]
    meal_blocks: list[str]
    pantry_on_hand_count: int
    pantry_staples_count: int
    macro_tracking: MacroTrackingConfig
    onboarding_step: str | None = None


class FamilySettingsService:
    """Read and update family settings stored in SQLite."""

    def __init__(
        self,
        db_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db_path = db_path or self.settings.database_path
        self.resolver = FamilyResolver(db_path=self.db_path, settings=self.settings)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def for_slack_channel(self, channel_id: str | None) -> FamilyContext:
        return self.resolver.for_slack_channel(channel_id)

    def for_slack_workspace(
        self,
        workspace_id: str | None,
        channel_id: str | None = None,
        slack_user_id: str | None = None,
    ) -> FamilyContext:
        return self.resolver.for_slack_workspace(workspace_id, channel_id, slack_user_id)

    def get_summary(self, family_id: str) -> SettingsSummary:
        ctx = self.resolver.for_family_id(family_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT onboarding_step, macro_tracking_json FROM family_settings WHERE family_id = ?",
                (family_id,),
            ).fetchone()
        finally:
            conn.close()

        macro_raw = {}
        onboarding_step = None
        if row:
            macro_raw = json.loads(row["macro_tracking_json"] or "{}")
            onboarding_step = row["onboarding_step"]

        members = []
        for member in ctx.profile.members:
            macro = self.get_member_macro_goals(family_id, member.id)
            members.append(
                {
                    "id": member.id,
                    "name": member.name,
                    "role": member.role.value,
                    "age_years": member.age_years,
                    "age_months": member.age_months,
                    "constraints": member.constraints,
                    "notes": member.notes,
                    "macro_goals": macro.model_dump() if macro else None,
                }
            )

        on_hand = sum(
            1
            for _ in ctx.pantry.on_hand
        )
        staples = len(ctx.pantry.weekly_staples)

        return SettingsSummary(
            family_id=family_id,
            timezone=ctx.profile.timezone,
            members=members,
            dietary_household=ctx.planning.dietary_household,
            cuisine_preferences=ctx.planning.cuisine_preferences,
            staple_patterns=ctx.planning.staple_patterns,
            schedule=ctx.profile.schedule,
            meal_blocks=ctx.profile.meal_blocks,
            pantry_on_hand_count=on_hand,
            pantry_staples_count=staples,
            macro_tracking=MacroTrackingConfig.model_validate(macro_raw or {}),
            onboarding_step=onboarding_step,
        )

    def get_member_macro_goals(self, family_id: str, member_id: str) -> MacroGoals | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT macro_goals_json FROM family_members
                WHERE family_id = ? AND id = ?
                """,
                (family_id, member_id),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
        if not row or not row["macro_goals_json"]:
            return None
        return MacroGoals.model_validate(json.loads(row["macro_goals_json"]))

    def format_slack_summary(self, summary: SettingsSummary) -> str:
        """Format settings for Slack Block Kit sections."""
        lines: list[str] = []

        lines.append(f"*Timezone:* {summary.timezone}")

        if summary.dietary_household:
            lines.append(f"*Household diet:* {', '.join(summary.dietary_household)}")
        else:
            lines.append("*Household diet:* _(none set)_")

        if summary.cuisine_preferences:
            lines.append(f"*Cuisines:* {', '.join(summary.cuisine_preferences)}")

        member_lines = []
        for m in summary.members:
            age = ""
            if m.get("age_months") is not None:
                age = f", {m['age_months']}mo"
            elif m.get("age_years") is not None:
                age = f", {m['age_years']}y"
            constraints = m.get("constraints") or {}
            diet_keys = [k for k, v in constraints.items() if v is True and k not in {
                "baby_led_weaning", "age_appropriate_foods", "variable_breakfast",
                "simple_lunches", "bulk_prep_lunch", "quick_lunches", "can_eat_like_adults",
            }]
            extra = f" — {', '.join(diet_keys)}" if diet_keys else ""
            member_lines.append(f"• *{m['name']}* ({m['role']}{age}){extra}")
        lines.append("*Members:*\n" + "\n".join(member_lines))

        if summary.schedule:
            sched = ", ".join(f"{k}: {v}" for k, v in summary.schedule.items())
            lines.append(f"*Schedule:* {sched}")

        lines.append(
            f"*Pantry:* {summary.pantry_on_hand_count} on-hand items, "
            f"{summary.pantry_staples_count} weekly staples"
        )

        if summary.macro_tracking.enabled:
            lines.append("*Macro tracking:* enabled")
        else:
            lines.append("*Macro tracking:* off _(say `track macros` to opt in)_")

        lines.append("")
        lines.append(
            "_Edit:_ `remove keto`, `add keto Alex`, `set household diet <diet>`, "
            "`settings pantry add/remove <item>`, `add member <name> <role> <age>`"
        )
        return "\n".join(lines)

    def _ensure_family_settings_row(self, conn: sqlite3.Connection, family_id: str) -> None:
        row = conn.execute(
            "SELECT family_id FROM family_settings WHERE family_id = ?",
            (family_id,),
        ).fetchone()
        if row:
            return
        conn.execute(
            """
            INSERT INTO family_settings
            (family_id, meal_blocks_json, schedule_json, planning_json, updated_at)
            VALUES (?, '[]', '{}', '{}', ?)
            """,
            (family_id, _utcnow()),
        )

    @staticmethod
    def _normalize_constraint_key(name: str) -> str:
        cleaned = re.sub(r"[^a-z0-9_\s-]", "", name.strip().lower())
        return cleaned.replace("-", "_").replace(" ", "_")

    def _find_member_row(
        self,
        conn: sqlite3.Connection,
        family_id: str,
        member_name: str,
    ) -> sqlite3.Row:
        rows = conn.execute(
            """
            SELECT id, display_name, constraints_json
            FROM family_members
            WHERE family_id = ?
            ORDER BY sort_order, display_name
            """,
            (family_id,),
        ).fetchall()
        if not rows:
            raise ValueError("No family members found.")
        needle = member_name.strip().lower()
        for row in rows:
            display = (row["display_name"] or "").lower()
            if display == needle or needle in display:
                return row
        names = ", ".join(row["display_name"] for row in rows)
        raise ValueError(f"Member not found: {member_name}. Known members: {names}")

    def add_member(
        self,
        family_id: str,
        *,
        name: str,
        role: str,
        age_years: float | None = None,
        age_months: float | None = None,
    ) -> str:
        member_id = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()) or str(uuid.uuid4())
        try:
            MemberRole(role.strip().lower())
        except ValueError as exc:
            valid = ", ".join(r.value for r in MemberRole)
            raise ValueError(f"Invalid role `{role}`. Use one of: {valid}") from exc

        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            existing = conn.execute(
                "SELECT id FROM family_members WHERE family_id = ? AND id = ?",
                (family_id, member_id),
            ).fetchone()
            if existing:
                member_id = str(uuid.uuid4())
            sort_row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM family_members WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            sort_order = int(sort_row[0]) if sort_row else 0
            conn.execute(
                """
                INSERT INTO family_members
                (id, family_id, display_name, role, age_years, age_months, constraints_json, notes, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, '{}', '', ?)
                """,
                (
                    member_id,
                    family_id,
                    name.strip(),
                    role.strip().lower(),
                    age_years,
                    age_months,
                    sort_order,
                ),
            )
            conn.execute(
                "UPDATE family_settings SET updated_at = ? WHERE family_id = ?",
                (_utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()
        return member_id

    def add_member_constraint(
        self,
        family_id: str,
        constraint: str,
        member_name: str,
    ) -> str:
        key = self._normalize_constraint_key(constraint)
        if not key:
            raise ValueError("Constraint name is required.")

        conn = self._connect()
        try:
            row = self._find_member_row(conn, family_id, member_name)
            constraints = json.loads(row["constraints_json"] or "{}")
            constraints[key] = True
            conn.execute(
                """
                UPDATE family_members
                SET constraints_json = ?
                WHERE family_id = ? AND id = ?
                """,
                (json.dumps(constraints), family_id, row["id"]),
            )
            conn.execute(
                "UPDATE family_settings SET updated_at = ? WHERE family_id = ?",
                (_utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()
        return key

    def remove_member_constraint(
        self,
        family_id: str,
        constraint: str,
        member_name: str | None = None,
    ) -> list[str]:
        key = self._normalize_constraint_key(constraint)
        if not key:
            raise ValueError("Constraint name is required.")

        conn = self._connect()
        removed_from: list[str] = []
        try:
            if member_name:
                row = self._find_member_row(conn, family_id, member_name)
                members = [row]
            else:
                members = conn.execute(
                    """
                    SELECT id, display_name, constraints_json
                    FROM family_members
                    WHERE family_id = ?
                    """,
                    (family_id,),
                ).fetchall()

            for row in members:
                constraints = json.loads(row["constraints_json"] or "{}")
                if key not in constraints:
                    continue
                del constraints[key]
                conn.execute(
                    """
                    UPDATE family_members
                    SET constraints_json = ?
                    WHERE family_id = ? AND id = ?
                    """,
                    (json.dumps(constraints), family_id, row["id"]),
                )
                removed_from.append(row["display_name"])

            if not removed_from:
                target = member_name or "any member"
                raise ValueError(f"Constraint `{key}` not set for {target}.")

            conn.execute(
                "UPDATE family_settings SET updated_at = ? WHERE family_id = ?",
                (_utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()
        return removed_from

    def set_household_diet(self, family_id: str, diets: list[str]) -> list[str]:
        normalized = [self._normalize_constraint_key(d) for d in diets if d.strip()]
        if not normalized:
            raise ValueError("Provide at least one diet label, e.g. `set household diet gluten_free`.")

        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            conn.execute(
                """
                UPDATE family_settings
                SET dietary_household_json = ?, updated_at = ?
                WHERE family_id = ?
                """,
                (json.dumps(normalized), _utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()
        return normalized

    def add_pantry_item(
        self,
        family_id: str,
        item: str,
        *,
        staple: bool = False,
    ) -> str:
        name = item.strip()
        if not name:
            raise ValueError("Pantry item name is required.")
        category = "weekly_staple" if staple else "on_hand_general"
        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            conn.execute(
                """
                INSERT OR IGNORE INTO family_pantry (family_id, category, item_name)
                VALUES (?, ?, ?)
                """,
                (family_id, category, name),
            )
            conn.execute(
                "UPDATE family_settings SET updated_at = ? WHERE family_id = ?",
                (_utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()
        return _normalize_name(name)

    def remove_pantry_item(self, family_id: str, item: str) -> str:
        needle = _normalize_name(item)
        if not needle:
            raise ValueError("Pantry item name is required.")

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT category, item_name FROM family_pantry WHERE family_id = ?",
                (family_id,),
            ).fetchall()
            removed_name = ""
            for row in rows:
                if _normalize_name(row["item_name"]) == needle or needle in _normalize_name(row["item_name"]):
                    conn.execute(
                        """
                        DELETE FROM family_pantry
                        WHERE family_id = ? AND category = ? AND item_name = ?
                        """,
                        (family_id, row["category"], row["item_name"]),
                    )
                    removed_name = row["item_name"]
                    break
            if not removed_name:
                raise ValueError(f"Pantry item not found: {item}")
            conn.execute(
                "UPDATE family_settings SET updated_at = ? WHERE family_id = ?",
                (_utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()
        return removed_name

    def get_profile_onboarding(self, family_id: str) -> tuple[str | None, dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT onboarding_step, onboarding_data_json
                FROM family_settings
                WHERE family_id = ?
                """,
                (family_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None, {}
        step = (row["onboarding_step"] or "").strip() or None
        data = json.loads(row["onboarding_data_json"] or "{}")
        return step, data

    def start_profile_onboarding(
        self,
        family_id: str,
        *,
        household_name: str,
        thread_ts: str = "",
    ) -> None:
        data = {"household_name": household_name.strip(), "thread_ts": thread_ts.strip()}
        self._write_profile_onboarding(family_id, "diet", data)

    def set_profile_onboarding_step(
        self,
        family_id: str,
        step: str,
        data: dict[str, Any],
    ) -> None:
        self._write_profile_onboarding(family_id, step, data)

    def complete_profile_onboarding(self, family_id: str) -> None:
        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            conn.execute(
                """
                UPDATE family_settings
                SET onboarding_step = ?, onboarding_data_json = ?, updated_at = ?
                WHERE family_id = ?
                """,
                ("complete", "{}", _utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()

    def apply_profile_diet_answer(
        self,
        family_id: str,
        diets: list[str],
        notes: str,
    ) -> None:
        if diets:
            self.set_household_diet(family_id, diets)
        if notes and not self._is_skip_text(notes):
            self._merge_planning_json(
                family_id,
                {"diet_notes": notes},
            )

    def apply_profile_fitness_answer(self, family_id: str, goal: str) -> None:
        if not goal:
            return
        self._merge_planning_json(family_id, {"nutrition_goal": goal})
        if goal in {"cut", "bulk", "weightlifting"}:
            self.set_macro_tracking_enabled(
                family_id,
                enabled=True,
                notes=f"Enabled during signup ({goal}).",
            )

    def apply_profile_cuisine_answer(
        self,
        family_id: str,
        cuisines: list[str],
        *,
        likes: str = "",
        avoid: str = "",
    ) -> None:
        if cuisines:
            self.set_cuisine_preferences(family_id, cuisines)
        updates: dict[str, str] = {}
        if likes:
            updates["food_likes"] = likes
        if avoid:
            updates["foods_avoid"] = avoid
        if updates:
            self._merge_planning_json(family_id, updates)

    def apply_profile_eaters_answer(
        self,
        family_id: str,
        members: list[tuple[str, str]],
    ) -> None:
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT COUNT(*) AS count FROM family_members WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            if existing and int(existing["count"] or 0) > 0:
                return
        finally:
            conn.close()
        for name, role in members:
            self.add_member(family_id, name=name, role=role)

    def set_cuisine_preferences(self, family_id: str, cuisines: list[str]) -> list[str]:
        normalized = [c.strip() for c in cuisines if c.strip()]
        if not normalized:
            raise ValueError("Provide at least one cuisine preference.")
        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            conn.execute(
                """
                UPDATE family_settings
                SET cuisine_preferences_json = ?, updated_at = ?
                WHERE family_id = ?
                """,
                (json.dumps(normalized), _utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()
        return normalized

    def set_macro_tracking_enabled(
        self,
        family_id: str,
        *,
        enabled: bool,
        notes: str = "",
    ) -> None:
        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            row = conn.execute(
                "SELECT macro_tracking_json FROM family_settings WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            current = json.loads(row["macro_tracking_json"] or "{}") if row else {}
            current["enabled"] = enabled
            if notes:
                current["notes"] = notes
            conn.execute(
                """
                UPDATE family_settings
                SET macro_tracking_json = ?, updated_at = ?
                WHERE family_id = ?
                """,
                (json.dumps(current), _utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _is_skip_text(text: str) -> bool:
        return text.strip().lower() in {"skip", "none", "no", "n/a"}

    def _merge_planning_json(self, family_id: str, updates: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            row = conn.execute(
                "SELECT planning_json FROM family_settings WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            planning = json.loads(row["planning_json"] or "{}") if row else {}
            planning.update(updates)
            conn.execute(
                """
                UPDATE family_settings
                SET planning_json = ?, updated_at = ?
                WHERE family_id = ?
                """,
                (json.dumps(planning), _utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_profile_onboarding(
        self,
        family_id: str,
        step: str,
        data: dict[str, Any],
    ) -> None:
        conn = self._connect()
        try:
            self._ensure_family_settings_row(conn, family_id)
            conn.execute(
                """
                UPDATE family_settings
                SET onboarding_step = ?, onboarding_data_json = ?, updated_at = ?
                WHERE family_id = ?
                """,
                (step, json.dumps(data), _utcnow(), family_id),
            )
            conn.commit()
        finally:
            conn.close()

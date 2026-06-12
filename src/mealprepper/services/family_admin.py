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
from mealprepper.storage.migrations import DEFAULT_FAMILY_ID

_FAMILY_SCOPED_TABLES: tuple[str, ...] = (
    "weekly_plans",
    "grocery_lists",
    "meal_feedback",
    "preferences",
    "preference_summaries",
    "approval_requests",
    "inventory",
    "recipe_repository",
    "meal_index",
    "feedback_index",
    "plan_index",
    "daily_macro_logs",
    "family_members",
    "family_settings",
    "family_pantry",
    "family_slack_users",
    "slack_conversations",
)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "household"


def _parse_slack_user_links(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FamilyRecord:
    id: str
    name: str
    slug: str
    timezone: str
    status: str
    member_count: int
    recipe_count: int = 0
    plan_count: int = 0
    created_at: str = ""
    slack_workspace_id: str | None = None
    slack_channel_id: str | None = None
    slack_user_links: list[str] | None = None  # "workspace:user_id"


@dataclass
class SlackUserHousehold:
    workspace_id: str
    slack_user_id: str
    family_id: str
    family_slug: str
    family_name: str
    created_at: str


@dataclass
class FamilyDetail:
    id: str
    name: str
    slug: str
    timezone: str
    status: str
    created_at: str
    updated_at: str
    member_count: int
    recipe_count: int
    plan_count: int
    members: list[dict[str, Any]]
    dietary_household: list[str]
    slack_bindings: list[dict[str, str]]
    slack_users: list[dict[str, str]]


@dataclass
class FamilyDeleteResult:
    family_id: str
    slug: str
    name: str
    dry_run: bool
    deleted_rows: dict[str, int]
    slack_bindings_cleared: int
    slack_users_cleared: int


class FamilyAdminService:
    """Create families and Slack workspace bindings from the CLI."""

    def __init__(
        self,
        db_path: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db_path = db_path or self.settings.database_path

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_families(self) -> list[FamilyRecord]:
        conn = self._connect()
        try:
            has_slack_users = self._table_exists(conn, "family_slack_users")
            slack_user_subquery = ""
            if has_slack_users:
                slack_user_subquery = """
                    , (
                        SELECT GROUP_CONCAT(fsu.workspace_id || ':' || fsu.slack_user_id, ', ')
                        FROM family_slack_users fsu
                        WHERE fsu.family_id = f.id
                    ) AS slack_user_links
                """
            rows = conn.execute(
                f"""
                SELECT f.id, f.name, f.slug, f.timezone, f.status, f.created_at,
                       (SELECT COUNT(*) FROM family_members m WHERE m.family_id = f.id) AS member_count,
                       (SELECT COUNT(*) FROM recipe_repository r WHERE r.family_id = f.id) AS recipe_count,
                       (SELECT COUNT(*) FROM weekly_plans p WHERE p.family_id = f.id) AS plan_count,
                       sb.workspace_id, sb.channel_id
                       {slack_user_subquery}
                FROM families f
                LEFT JOIN slack_bindings sb ON sb.family_id = f.id
                ORDER BY f.slug
                """
            ).fetchall()
        finally:
            conn.close()
        return [
            FamilyRecord(
                id=row["id"],
                name=row["name"],
                slug=row["slug"] or row["id"],
                timezone=row["timezone"],
                status=row["status"],
                member_count=int(row["member_count"] or 0),
                recipe_count=int(row["recipe_count"] or 0),
                plan_count=int(row["plan_count"] or 0),
                created_at=row["created_at"] or "",
                slack_workspace_id=row["workspace_id"],
                slack_channel_id=row["channel_id"],
                slack_user_links=_parse_slack_user_links(
                    row["slack_user_links"] if has_slack_users and "slack_user_links" in row.keys() else ""
                ),
            )
            for row in rows
        ]

    def list_slack_user_households(
        self,
        workspace_id: str = "",
    ) -> list[SlackUserHousehold]:
        """List per-user household mappings (Slack `start` onboarding)."""
        conn = self._connect()
        try:
            if not self._table_exists(conn, "family_slack_users"):
                return []
            query = """
                SELECT fsu.workspace_id, fsu.slack_user_id, fsu.created_at,
                       f.id AS family_id, f.slug, f.name
                FROM family_slack_users fsu
                JOIN families f ON f.id = fsu.family_id
            """
            params: list[str] = []
            if workspace_id.strip():
                query += " WHERE fsu.workspace_id = ?"
                params.append(workspace_id.strip())
            query += " ORDER BY fsu.workspace_id, fsu.slack_user_id"
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
        return [
            SlackUserHousehold(
                workspace_id=row["workspace_id"],
                slack_user_id=row["slack_user_id"],
                family_id=row["family_id"],
                family_slug=row["slug"] or row["family_id"],
                family_name=row["name"],
                created_at=row["created_at"] or "",
            )
            for row in rows
        ]

    def get_slack_user_household(
        self,
        workspace_id: str,
        slack_user_id: str,
    ) -> SlackUserHousehold | None:
        """Return one user's household mapping, if they finished Slack onboarding."""
        workspace_id = workspace_id.strip()
        slack_user_id = slack_user_id.strip()
        if not workspace_id or not slack_user_id:
            return None
        for row in self.list_slack_user_households(workspace_id=workspace_id):
            if row.slack_user_id == slack_user_id:
                return row
        return None

    def get_family_detail(self, slug: str) -> FamilyDetail:
        conn = self._connect()
        try:
            family = conn.execute(
                "SELECT * FROM families WHERE slug = ? OR id = ?",
                (slug, slug),
            ).fetchone()
            if not family:
                raise ValueError(f"Family not found: {slug}")

            family_id = family["id"]
            settings_row = conn.execute(
                "SELECT dietary_household_json FROM family_settings WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            members = conn.execute(
                """
                SELECT display_name, role, age_years, age_months
                FROM family_members
                WHERE family_id = ?
                ORDER BY sort_order, display_name
                """,
                (family_id,),
            ).fetchall()
            bindings = conn.execute(
                """
                SELECT workspace_id, channel_id, webhook_url,
                       COALESCE(bot_token, '') AS bot_token
                FROM slack_bindings
                WHERE family_id = ?
                """,
                (family_id,),
            ).fetchall()
            slack_users: list[sqlite3.Row] = []
            if self._table_exists(conn, "family_slack_users"):
                slack_users = conn.execute(
                    """
                    SELECT workspace_id, slack_user_id, created_at
                    FROM family_slack_users
                    WHERE family_id = ?
                    ORDER BY workspace_id, slack_user_id
                    """,
                    (family_id,),
                ).fetchall()
            member_count = conn.execute(
                "SELECT COUNT(*) AS count FROM family_members WHERE family_id = ?",
                (family_id,),
            ).fetchone()["count"]
            recipe_count = conn.execute(
                "SELECT COUNT(*) AS count FROM recipe_repository WHERE family_id = ?",
                (family_id,),
            ).fetchone()["count"]
            plan_count = conn.execute(
                "SELECT COUNT(*) AS count FROM weekly_plans WHERE family_id = ?",
                (family_id,),
            ).fetchone()["count"]
        finally:
            conn.close()

        dietary = []
        if settings_row and settings_row["dietary_household_json"]:
            dietary = json.loads(settings_row["dietary_household_json"])

        return FamilyDetail(
            id=family_id,
            name=family["name"],
            slug=family["slug"] or family_id,
            timezone=family["timezone"],
            status=family["status"],
            created_at=family["created_at"] or "",
            updated_at=family["updated_at"] or "",
            member_count=int(member_count or 0),
            recipe_count=int(recipe_count or 0),
            plan_count=int(plan_count or 0),
            members=[dict(row) for row in members],
            dietary_household=dietary,
            slack_bindings=[
                {
                    "workspace_id": row["workspace_id"],
                    "channel_id": row["channel_id"],
                    "webhook_url": row["webhook_url"] or "",
                    "bot_token_set": bool((row["bot_token"] or "").strip()),
                }
                for row in bindings
            ],
            slack_users=[dict(row) for row in slack_users],
        )

    def add_slack_binding(
        self,
        *,
        slug: str,
        name: str,
        workspace_id: str,
        channel_id: str,
        webhook_url: str = "",
        bot_token: str = "",
        timezone: str = "America/New_York",
    ) -> FamilyDetail:
        slug = slug.strip().lower().replace(" ", "-")
        workspace_id = workspace_id.strip()
        channel_id = channel_id.strip()
        if not slug:
            raise ValueError("Family slug is required.")
        if not workspace_id:
            raise ValueError("workspace_id (Slack team_id) is required.")
        if not channel_id:
            raise ValueError("channel_id is required.")

        family_id = slug
        now = _utcnow()
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT id FROM families WHERE id = ? OR slug = ?",
                (family_id, slug),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO families (id, name, slug, timezone, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (family_id, name.strip() or slug, slug, timezone, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO family_settings
                    (family_id, meal_blocks_json, schedule_json, planning_json, updated_at)
                    VALUES (?, '[]', '{}', '{}', ?)
                    """,
                    (family_id, now),
                )
            else:
                family_id = existing["id"]
                conn.execute(
                    "UPDATE families SET name = ?, timezone = ?, updated_at = ? WHERE id = ?",
                    (name.strip() or slug, timezone, now, family_id),
                )

            binding = conn.execute(
                "SELECT id FROM slack_bindings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if binding:
                conn.execute(
                    """
                    UPDATE slack_bindings
                    SET family_id = ?, channel_id = ?, webhook_url = ?, bot_token = ?
                    WHERE workspace_id = ?
                    """,
                    (
                        family_id,
                        channel_id,
                        webhook_url.strip(),
                        bot_token.strip(),
                        workspace_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO slack_bindings
                    (id, family_id, workspace_id, channel_id, webhook_url, bot_token, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        family_id,
                        workspace_id,
                        channel_id,
                        webhook_url.strip(),
                        bot_token.strip(),
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        return self.get_family_detail(slug)

    def bind_workspace(
        self,
        *,
        workspace_id: str,
        bot_token: str,
        channel_id: str = "",
        webhook_url: str = "",
    ) -> dict[str, str]:
        """Register a Slack workspace install before any household (family) exists."""
        workspace_id = workspace_id.strip()
        if not workspace_id:
            raise ValueError("workspace_id (Slack team_id) is required.")
        if not bot_token.strip():
            raise ValueError("bot_token is required.")

        now = _utcnow()
        conn = self._connect()
        try:
            binding = conn.execute(
                "SELECT id, family_id FROM slack_bindings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if binding:
                conn.execute(
                    """
                    UPDATE slack_bindings
                    SET channel_id = ?, webhook_url = ?, bot_token = ?
                    WHERE workspace_id = ?
                    """,
                    (
                        channel_id.strip(),
                        webhook_url.strip(),
                        bot_token.strip(),
                        workspace_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO slack_bindings
                    (id, family_id, workspace_id, channel_id, webhook_url, bot_token, created_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        workspace_id,
                        channel_id.strip(),
                        webhook_url.strip(),
                        bot_token.strip(),
                        now,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        return {
            "workspace_id": workspace_id,
            "channel_id": channel_id.strip(),
            "bot_token_set": True,
            "family_id": (binding["family_id"] or "") if binding else "",
        }

    def create_household_for_slack_user(
        self,
        *,
        workspace_id: str,
        slack_user_id: str,
        name: str,
        timezone: str = "America/New_York",
    ) -> FamilyDetail:
        """Create a household for one Slack user in a workspace-only install."""
        workspace_id = workspace_id.strip()
        slack_user_id = slack_user_id.strip()
        display_name = name.strip()
        if not workspace_id:
            raise ValueError("workspace_id is required.")
        if not slack_user_id:
            raise ValueError("slack_user_id is required.")
        if not display_name:
            raise ValueError("Household name is required.")

        now = _utcnow()
        conn = self._connect()
        try:
            binding = conn.execute(
                "SELECT id, family_id FROM slack_bindings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if not binding:
                raise ValueError(f"Slack workspace not registered: {workspace_id}")
            if (binding["family_id"] or "").strip():
                raise ValueError(
                    "This workspace uses a shared household binding; per-user setup is not available."
                )

            existing = conn.execute(
                """
                SELECT family_id FROM family_slack_users
                WHERE workspace_id = ? AND slack_user_id = ?
                """,
                (workspace_id, slack_user_id),
            ).fetchone()
            if existing:
                return self.get_family_detail(existing["family_id"])

            base_slug = _slugify(display_name)
            slug = base_slug
            suffix = 2
            while conn.execute(
                "SELECT 1 FROM families WHERE id = ? OR slug = ?",
                (slug, slug),
            ).fetchone():
                slug = f"{base_slug}-{suffix}"
                suffix += 1

            family_id = slug
            conn.execute(
                """
                INSERT INTO families (id, name, slug, timezone, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (family_id, display_name, slug, timezone, now, now),
            )
            conn.execute(
                """
                INSERT INTO family_settings
                (family_id, meal_blocks_json, schedule_json, planning_json, updated_at)
                VALUES (?, '[]', '{}', '{}', ?)
                """,
                (family_id, now),
            )
            conn.execute(
                """
                INSERT INTO family_slack_users (workspace_id, slack_user_id, family_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (workspace_id, slack_user_id, family_id, now),
            )
            conn.commit()
        finally:
            conn.close()

        return self.get_family_detail(slug)

    def delete_family(
        self,
        slug: str,
        *,
        dry_run: bool = False,
        force_default: bool = False,
    ) -> FamilyDeleteResult:
        """Delete a household and all tenant data scoped to it."""
        detail = self.get_family_detail(slug)
        if detail.id == DEFAULT_FAMILY_ID and not force_default:
            raise ValueError(
                f"Refusing to delete `{DEFAULT_FAMILY_ID}`. Pass force_default=True to override."
            )

        family_id = detail.id
        deleted_rows: dict[str, int] = {}
        slack_bindings_cleared = 0
        slack_users_cleared = 0

        conn = self._connect()
        try:
            for table in _FAMILY_SCOPED_TABLES:
                if not self._table_exists(conn, table):
                    continue
                if not self._column_exists(conn, table, "family_id"):
                    continue
                count = conn.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE family_id = ?",
                    (family_id,),
                ).fetchone()["count"]
                deleted_rows[table] = int(count or 0)

            if self._table_exists(conn, "family_slack_users"):
                slack_users_cleared = conn.execute(
                    "SELECT COUNT(*) AS count FROM family_slack_users WHERE family_id = ?",
                    (family_id,),
                ).fetchone()["count"]
                slack_users_cleared = int(slack_users_cleared or 0)

            if self._table_exists(conn, "slack_bindings"):
                slack_bindings_cleared = conn.execute(
                    "SELECT COUNT(*) AS count FROM slack_bindings WHERE family_id = ?",
                    (family_id,),
                ).fetchone()["count"]
                slack_bindings_cleared = int(slack_bindings_cleared or 0)

            if dry_run:
                return FamilyDeleteResult(
                    family_id=family_id,
                    slug=detail.slug,
                    name=detail.name,
                    dry_run=True,
                    deleted_rows=deleted_rows,
                    slack_bindings_cleared=slack_bindings_cleared,
                    slack_users_cleared=slack_users_cleared,
                )

            for table in _FAMILY_SCOPED_TABLES:
                if not self._table_exists(conn, table):
                    continue
                if not self._column_exists(conn, table, "family_id"):
                    continue
                conn.execute(f"DELETE FROM {table} WHERE family_id = ?", (family_id,))

            if self._table_exists(conn, "slack_bindings"):
                conn.execute(
                    "UPDATE slack_bindings SET family_id = NULL WHERE family_id = ?",
                    (family_id,),
                )

            conn.execute("DELETE FROM families WHERE id = ?", (family_id,))
            conn.commit()
        finally:
            conn.close()

        return FamilyDeleteResult(
            family_id=family_id,
            slug=detail.slug,
            name=detail.name,
            dry_run=False,
            deleted_rows=deleted_rows,
            slack_bindings_cleared=slack_bindings_cleared,
            slack_users_cleared=slack_users_cleared,
        )

    @staticmethod
    def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == column for row in rows)

    def list_workspace_bindings(self) -> list[dict[str, str]]:
        """Return all workspace bindings, including those pending household onboarding."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT sb.workspace_id, sb.channel_id, sb.webhook_url,
                       COALESCE(sb.bot_token, '') AS bot_token,
                       sb.family_id, f.slug AS family_slug, f.name AS family_name
                FROM slack_bindings sb
                LEFT JOIN families f ON f.id = sb.family_id
                ORDER BY sb.workspace_id
                """
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "workspace_id": row["workspace_id"],
                "channel_id": row["channel_id"] or "",
                "webhook_url": row["webhook_url"] or "",
                "bot_token_set": bool((row["bot_token"] or "").strip()),
                "family_id": row["family_id"] or "",
                "family_slug": row["family_slug"] or "",
                "family_name": row["family_name"] or "",
            }
            for row in rows
        ]

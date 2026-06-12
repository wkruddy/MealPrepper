from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mealprepper.config import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_FAMILY_ID = "default"
MIGRATION_VERSIONS = (
    "001_multi_family",
    "002_macro_tracking",
    "003_slack_workspace_tenant",
    "004_slack_workspace_pending",
    "005_family_slack_users",
)

_TENANT_TABLES = (
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
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrations_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.project_root / "migrations"


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


def _ensure_family_id_columns(conn: sqlite3.Connection) -> None:
    for table in _TENANT_TABLES:
        if not _table_exists(conn, table):
            continue
        if _column_exists(conn, table, "family_id"):
            continue
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN family_id TEXT NOT NULL DEFAULT '{DEFAULT_FAMILY_ID}'"
        )
        logger.info("Added family_id column to %s", table)


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_weekly_plans_family_created ON weekly_plans(family_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_grocery_lists_family ON grocery_lists(family_id)",
        "CREATE INDEX IF NOT EXISTS idx_meal_feedback_family ON meal_feedback(family_id)",
        "CREATE INDEX IF NOT EXISTS idx_preferences_family ON preferences(family_id, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_preference_summaries_family ON preference_summaries(family_id)",
        "CREATE INDEX IF NOT EXISTS idx_approval_requests_family ON approval_requests(family_id)",
        "CREATE INDEX IF NOT EXISTS idx_inventory_family ON inventory(family_id)",
        "CREATE INDEX IF NOT EXISTS idx_recipe_repository_family ON recipe_repository(family_id, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_meal_index_family ON meal_index(family_id)",
        "CREATE INDEX IF NOT EXISTS idx_feedback_index_family ON feedback_index(family_id)",
        "CREATE INDEX IF NOT EXISTS idx_plan_index_family ON plan_index(family_id)",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_recipe_family_content_hash
        ON recipe_repository(family_id, content_hash)
        WHERE content_hash IS NOT NULL AND content_hash != ''
        """,
    ]
    for statement in index_statements:
        table = statement.split(" ON ", 1)[1].split("(", 1)[0].strip()
        if not _table_exists(conn, table) or not _column_exists(conn, table, "family_id"):
            continue
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            logger.debug("Index skipped (%s): %s", statement[:40], exc)


def _backfill_family_id(conn: sqlite3.Connection) -> None:
    for table in _TENANT_TABLES:
        if not _table_exists(conn, table) or not _column_exists(conn, table, "family_id"):
            continue
        conn.execute(
            f"UPDATE {table} SET family_id = ? WHERE family_id IS NULL OR family_id = ''",
            (DEFAULT_FAMILY_ID,),
        )


def _migration_applied(conn: sqlite3.Connection, version: str) -> bool:
    if not _table_exists(conn, "schema_migrations"):
        return False
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (version,),
    ).fetchone()
    return row is not None


def _mark_migration_applied(conn: sqlite3.Connection, version: str) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (version, _utcnow()),
    )


def _import_pantry_rows(conn: sqlite3.Connection, family_id: str, raw: dict[str, Any]) -> None:
    conn.execute("DELETE FROM family_pantry WHERE family_id = ?", (family_id,))
    for group_name, items in raw.get("on_hand", {}).items():
        if not isinstance(items, list):
            continue
        category = f"on_hand_{group_name}"
        for item in items:
            if isinstance(item, str) and item.strip():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO family_pantry (family_id, category, item_name)
                    VALUES (?, ?, ?)
                    """,
                    (family_id, category, item.strip()),
                )
    for item in raw.get("weekly_staples", []):
        if isinstance(item, str) and item.strip():
            conn.execute(
                """
                INSERT OR IGNORE INTO family_pantry (family_id, category, item_name)
                VALUES (?, 'weekly_staple', ?)
                """,
                (family_id, item.strip()),
            )


def _import_family_members(
    conn: sqlite3.Connection,
    family_id: str,
    members: list[dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM family_members WHERE family_id = ?", (family_id,))
    for order, member in enumerate(members):
        constraints = member.get("constraints", {})
        if isinstance(constraints, list):
            normalized: dict[str, Any] = {}
            for item in constraints:
                if isinstance(item, str):
                    normalized[item] = True
                elif isinstance(item, dict):
                    normalized.update(item)
            constraints = normalized
        conn.execute(
            """
            INSERT INTO family_members
            (id, family_id, display_name, role, age_years, age_months, constraints_json, notes, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member.get("id") or str(uuid.uuid4()),
                family_id,
                member.get("name") or member.get("display_name") or "Member",
                member.get("role", "adult"),
                member.get("age_years"),
                member.get("age_months"),
                json.dumps(constraints or {}),
                member.get("notes") or "",
                order,
            ),
        )


def seed_default_family(conn: sqlite3.Connection, settings: Settings | None = None) -> None:
    """Create default family and import config/family.yaml + pantry.yaml if missing."""
    settings = settings or get_settings()
    now = _utcnow()
    row = conn.execute("SELECT id FROM families WHERE id = ?", (DEFAULT_FAMILY_ID,)).fetchone()
    if not row:
        family_yaml = settings.load_yaml("family.yaml")
        pantry_yaml = settings.load_yaml("pantry.yaml")
        defaults_yaml = settings.load_yaml("default.yaml")
        family_name = family_yaml.get("name") or "Default Family"
        timezone = family_yaml.get("timezone") or settings.default_timezone
        conn.execute(
            """
            INSERT INTO families (id, name, slug, timezone, status, created_at, updated_at)
            VALUES (?, ?, 'default', ?, 'active', ?, ?)
            """,
            (DEFAULT_FAMILY_ID, family_name, timezone, now, now),
        )
        planning = defaults_yaml.get("planning", {})
        conn.execute(
            """
            INSERT INTO family_settings
            (family_id, meal_blocks_json, schedule_json, planning_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_FAMILY_ID,
                json.dumps(family_yaml.get("meal_blocks", [])),
                json.dumps(family_yaml.get("schedule", {})),
                json.dumps(planning),
                now,
            ),
        )
        _import_family_members(conn, DEFAULT_FAMILY_ID, family_yaml.get("members", []))
        _import_pantry_rows(conn, DEFAULT_FAMILY_ID, pantry_yaml)
        logger.info("Seeded default family from YAML config")
    else:
        conn.execute(
            "UPDATE families SET updated_at = ? WHERE id = ?",
            (now, DEFAULT_FAMILY_ID),
        )


def _apply_migration(
    conn: sqlite3.Connection,
    version: str,
    *,
    settings: Settings,
) -> None:
    sql_path = _migrations_dir(settings) / f"{version}.sql"
    if not sql_path.exists():
        logger.warning("Migration file missing: %s", sql_path)
        return
    conn.executescript(sql_path.read_text(encoding="utf-8"))
    _mark_migration_applied(conn, version)
    logger.info("Applied migration %s", version)


def run_migrations(
    conn: sqlite3.Connection,
    *,
    settings: Settings | None = None,
) -> None:
    """Apply pending SQL migrations and seed the default family."""
    settings = settings or get_settings()
    for version in MIGRATION_VERSIONS:
        if not _migration_applied(conn, version):
            _apply_migration(conn, version, settings=settings)

    _ensure_family_id_columns(conn)
    _ensure_indexes(conn)
    _backfill_family_id(conn)
    seed_default_family(conn, settings)

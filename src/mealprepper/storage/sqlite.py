from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from mealprepper.config import Settings, get_settings
from mealprepper.models.feedback import FeedbackRating, MealFeedback, PreferenceProfile
from mealprepper.storage.migrations import DEFAULT_FAMILY_ID, run_migrations
from mealprepper.models.grocery import GroceryList
from mealprepper.models.meals import MealRecipe
from mealprepper.models.recipe_repository import SavedRecipe
from mealprepper.models.plans import PlanStatus, WeeklyPlan

logger = logging.getLogger(__name__)

_schema_initialized: set[Path] = set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteStore:
    def __init__(
        self,
        db_path: Path | None = None,
        settings: Settings | None = None,
        family_id: str = DEFAULT_FAMILY_ID,
    ) -> None:
        self.settings = settings or get_settings()
        self.db_path = db_path or self.settings.database_path
        self.family_id = family_id
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _fid(self, family_id: str | None = None) -> str:
        return family_id or self.family_id

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS weekly_plans (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    week_start TEXT NOT NULL,
                    week_end TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    playbook_markdown TEXT,
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS grocery_lists (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    weekly_plan_id TEXT,
                    week_label TEXT,
                    payload TEXT NOT NULL,
                    ready_for_shopping INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (weekly_plan_id) REFERENCES weekly_plans(id)
                );

                CREATE TABLE IF NOT EXISTS meal_feedback (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    meal_title TEXT NOT NULL,
                    meal_block TEXT,
                    day TEXT,
                    rating TEXT NOT NULL,
                    comment TEXT,
                    member_id TEXT,
                    created_at TEXT NOT NULL,
                    applied INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    profile_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS preference_summaries (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    summary_text TEXT NOT NULL,
                    feedback_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inventory (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    item_name TEXT NOT NULL,
                    quantity TEXT,
                    category TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approval_requests (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    weekly_plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    response TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE TABLE IF NOT EXISTS meal_index (
                    meal_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    meal_block TEXT,
                    day TEXT,
                    ingredients TEXT,
                    tags TEXT,
                    plan_id TEXT,
                    body TEXT
                );

                CREATE TABLE IF NOT EXISTS feedback_index (
                    feedback_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    meal_title TEXT NOT NULL,
                    meal_block TEXT,
                    rating TEXT NOT NULL,
                    comment TEXT,
                    body TEXT
                );

                CREATE TABLE IF NOT EXISTS plan_index (
                    plan_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    week_start TEXT NOT NULL,
                    week_end TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT,
                    body TEXT
                );

                CREATE TABLE IF NOT EXISTS recipe_repository (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT,
                    source_label TEXT,
                    content_hash TEXT,
                    raw_text TEXT,
                    recipe_json TEXT,
                    meal_blocks TEXT,
                    tags TEXT,
                    notes TEXT,
                    favorite INTEGER DEFAULT 1,
                    body TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            run_migrations(conn, settings=self.settings)
            self._migrate_fts(conn)

    def _migrate_fts(self, conn: sqlite3.Connection) -> None:
        """Create FTS5 virtual tables if missing (idempotent migration)."""
        fts_defs = [
            (
                "meal_index_fts",
                "meal_index",
                "meal_id, title, meal_block, day, ingredients, tags, body",
            ),
            (
                "feedback_index_fts",
                "feedback_index",
                "feedback_id, meal_title, meal_block, rating, comment, body",
            ),
            (
                "plan_index_fts",
                "plan_index",
                "plan_id, week_start, week_end, status, summary, body",
            ),
            (
                "recipe_repository_fts",
                "recipe_repository",
                "title, meal_blocks, tags, notes, source_label, body",
            ),
        ]
        for fts_name, content_table, columns in fts_defs:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (fts_name,),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE {fts_name} USING fts5(
                    {columns},
                    content='{content_table}',
                    content_rowid='rowid'
                )
                """
            )
            conn.execute(
                f"""
                INSERT INTO {fts_name}({fts_name}) VALUES('rebuild')
                """
            )
            logger.info("Created FTS index: %s", fts_name)

        self._ensure_fts_triggers(conn)

    def _ensure_fts_triggers(self, conn: sqlite3.Connection) -> None:
        """Keep FTS5 indexes in sync with content tables."""
        trigger_defs = [
            (
                "meal_index",
                "meal_index_fts",
                "meal_id, title, meal_block, day, ingredients, tags, body",
            ),
            (
                "feedback_index",
                "feedback_index_fts",
                "feedback_id, meal_title, meal_block, rating, comment, body",
            ),
            (
                "plan_index",
                "plan_index_fts",
                "plan_id, week_start, week_end, status, summary, body",
            ),
            (
                "recipe_repository",
                "recipe_repository_fts",
                "title, meal_blocks, tags, notes, source_label, body",
            ),
        ]
        for content_table, fts_table, columns in trigger_defs:
            col_list = columns
            cols = [c.strip() for c in columns.split(",")]
            new_vals = ", ".join(f"new.{c}" for c in cols)
            for action in ("insert", "update", "delete"):
                trigger_name = f"{fts_table}_ai_{action[0]}"
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",
                    (trigger_name,),
                ).fetchone()
                if exists:
                    continue
                if action == "insert":
                    conn.execute(
                        f"""
                        CREATE TRIGGER {trigger_name} AFTER INSERT ON {content_table} BEGIN
                            INSERT INTO {fts_table}(rowid, {col_list})
                            VALUES (new.rowid, {new_vals});
                        END
                        """
                    )
                elif action == "update":
                    conn.execute(
                        f"""
                        CREATE TRIGGER {trigger_name} AFTER UPDATE ON {content_table} BEGIN
                            INSERT INTO {fts_table}({fts_table}) VALUES('delete');
                            INSERT INTO {fts_table}(rowid, {col_list})
                            VALUES (new.rowid, {new_vals});
                        END
                        """
                    )
                else:
                    conn.execute(
                        f"""
                        CREATE TRIGGER {trigger_name} AFTER DELETE ON {content_table} BEGIN
                            INSERT INTO {fts_table}({fts_table}, rowid) VALUES('delete', old.rowid);
                        END
                        """
                    )

    def save_weekly_plan(
        self,
        plan: WeeklyPlan,
        *,
        family_id: str | None = None,
    ) -> WeeklyPlan:
        fid = self._fid(family_id)
        plan_id = plan.id or str(uuid.uuid4())
        now = _utcnow().isoformat()
        payload = plan.model_dump(mode="json")
        payload["id"] = plan_id
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO weekly_plans
                (id, family_id, week_start, week_end, status, payload, playbook_markdown, created_at, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    fid,
                    plan.week_start.isoformat(),
                    plan.week_end.isoformat(),
                    plan.status.value,
                    json.dumps(payload),
                    plan.playbook_markdown,
                    plan.created_at.isoformat() if plan.created_at else now,
                    plan.approved_at.isoformat() if plan.approved_at else None,
                ),
            )
        plan.id = plan_id
        if not plan.created_at:
            plan.created_at = datetime.fromisoformat(now)
        self._index_plan(plan, family_id=fid)
        return plan

    def _index_plan(self, plan: WeeklyPlan, *, family_id: str | None = None) -> None:
        from mealprepper.index.meal_index import MealIndex
        from mealprepper.index.plan_index import PlanIndex

        fid = self._fid(family_id)
        try:
            MealIndex(db_path=self.db_path, settings=self.settings, family_id=fid).index_plan(plan)
            PlanIndex(db_path=self.db_path, settings=self.settings, family_id=fid).index_plan(plan)
        except Exception as exc:
            logger.warning("Failed to index plan %s: %s", plan.id, exc)

    def get_weekly_plan(
        self,
        plan_id: str,
        *,
        family_id: str | None = None,
    ) -> WeeklyPlan | None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM weekly_plans WHERE id = ? AND family_id = ?",
                (plan_id, fid),
            ).fetchone()
        if not row:
            return None
        return WeeklyPlan.model_validate(json.loads(row["payload"]))

    def get_latest_plan(
        self,
        status: PlanStatus | None = None,
        *,
        family_id: str | None = None,
    ) -> WeeklyPlan | None:
        fid = self._fid(family_id)
        query = "SELECT payload FROM weekly_plans WHERE family_id = ?"
        params: list = [fid]
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._conn() as conn:
            row = conn.execute(query, params).fetchone()
        if not row:
            return None
        return WeeklyPlan.model_validate(json.loads(row["payload"]))

    def get_plan_for_date(
        self,
        target: date,
        *,
        family_id: str | None = None,
    ) -> WeeklyPlan | None:
        """Return the best approved/active plan whose date range includes target."""
        fid = self._fid(family_id)
        active_statuses = {PlanStatus.APPROVED, PlanStatus.ACTIVE}
        covering: list[WeeklyPlan] = []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM weekly_plans WHERE family_id = ? ORDER BY created_at DESC",
                (fid,),
            ).fetchall()
        for row in rows:
            plan = WeeklyPlan.model_validate(json.loads(row["payload"]))
            if plan.week_start <= target <= plan.week_end and plan.status in active_statuses:
                covering.append(plan)
        if not covering:
            return None
        status_rank = {PlanStatus.ACTIVE: 2, PlanStatus.APPROVED: 1}
        covering.sort(
            key=lambda plan: (
                status_rank.get(plan.status, 0),
                plan.approved_at or plan.created_at or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        return covering[0]

    def list_recent_plans(
        self,
        limit: int = 10,
        *,
        family_id: str | None = None,
    ) -> list[WeeklyPlan]:
        fid = self._fid(family_id)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM weekly_plans WHERE family_id = ? ORDER BY created_at DESC LIMIT ?",
                (fid, limit),
            ).fetchall()
        return [WeeklyPlan.model_validate(json.loads(r["payload"])) for r in rows]

    def recent_dishes_by_block(
        self,
        week_start: date,
        *,
        lookback_weeks: int = 2,
        statuses: set[PlanStatus] | None = None,
        family_id: str | None = None,
    ) -> dict[str, set[str]]:
        """Meal titles from approved/active weeks in the lookback window before week_start."""
        if lookback_weeks < 1:
            return {}

        active_statuses = statuses or {
            PlanStatus.APPROVED,
            PlanStatus.ACTIVE,
            PlanStatus.COMPLETED,
        }
        cutoff_start = week_start - timedelta(weeks=lookback_weeks)
        dishes: dict[str, set[str]] = {}

        for plan in self.list_recent_plans(limit=30, family_id=family_id):
            if plan.week_end >= week_start:
                continue
            if plan.week_start < cutoff_start:
                continue
            if plan.status not in active_statuses:
                continue
            for meal in plan.meals:
                title = meal.recipe.title.strip()
                if title:
                    dishes.setdefault(meal.meal_block, set()).add(title)
        return dishes

    def list_recent_feedback(
        self,
        limit: int = 15,
        *,
        family_id: str | None = None,
    ) -> list[MealFeedback]:
        fid = self._fid(family_id)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM meal_feedback WHERE family_id = ? ORDER BY created_at DESC LIMIT ?",
                (fid, limit),
            ).fetchall()
        return [
            MealFeedback(
                id=r["id"],
                meal_title=r["meal_title"],
                meal_block=r["meal_block"] or "",
                day=r["day"] or "",
                rating=FeedbackRating(r["rating"]),
                comment=r["comment"] or "",
                member_id=r["member_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
                applied_to_preferences=bool(r["applied"]),
            )
            for r in rows
        ]

    def update_plan_status(
        self,
        plan_id: str,
        status: PlanStatus,
        *,
        family_id: str | None = None,
    ) -> None:
        plan = self.get_weekly_plan(plan_id, family_id=family_id)
        if not plan:
            raise ValueError(f"Plan not found: {plan_id}")
        plan.status = status
        if status == PlanStatus.APPROVED:
            plan.approved_at = _utcnow()
        self.save_weekly_plan(plan, family_id=family_id)

    def save_grocery_list(
        self,
        grocery: GroceryList,
        *,
        family_id: str | None = None,
    ) -> GroceryList:
        fid = self._fid(family_id)
        gid = grocery.id or str(uuid.uuid4())
        now = _utcnow().isoformat()
        payload = grocery.model_dump(mode="json")
        payload["id"] = gid
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO grocery_lists
                (id, family_id, weekly_plan_id, week_label, payload, ready_for_shopping, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gid,
                    fid,
                    grocery.weekly_plan_id,
                    grocery.week_label,
                    json.dumps(payload),
                    1 if grocery.ready_for_shopping else 0,
                    grocery.created_at.isoformat() if grocery.created_at else now,
                ),
            )
        grocery.id = gid
        return grocery

    def get_grocery_for_plan(
        self,
        plan_id: str,
        *,
        family_id: str | None = None,
    ) -> GroceryList | None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT payload FROM grocery_lists
                WHERE weekly_plan_id = ? AND family_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (plan_id, fid),
            ).fetchone()
        if not row:
            return None
        return GroceryList.model_validate(json.loads(row["payload"]))

    def get_grocery(
        self,
        grocery_id: str,
        *,
        family_id: str | None = None,
    ) -> GroceryList | None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM grocery_lists WHERE id = ? AND family_id = ?",
                (grocery_id, fid),
            ).fetchone()
        if not row:
            return None
        return GroceryList.model_validate(json.loads(row["payload"]))

    def get_latest_grocery(self, *, family_id: str | None = None) -> GroceryList | None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT payload FROM grocery_lists
                WHERE family_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (fid,),
            ).fetchone()
        if not row:
            return None
        return GroceryList.model_validate(json.loads(row["payload"]))

    def save_feedback(
        self,
        feedback: MealFeedback,
        *,
        family_id: str | None = None,
    ) -> MealFeedback:
        store_fid = self._fid(family_id)
        fid = feedback.id or str(uuid.uuid4())
        now = _utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO meal_feedback
                (id, family_id, meal_title, meal_block, day, rating, comment, member_id, created_at, applied)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fid,
                    store_fid,
                    feedback.meal_title,
                    feedback.meal_block,
                    feedback.day,
                    feedback.rating.value,
                    feedback.comment,
                    feedback.member_id,
                    feedback.created_at.isoformat() if feedback.created_at else now,
                    1 if feedback.applied_to_preferences else 0,
                ),
            )
        feedback.id = fid
        self._index_feedback(feedback, family_id=store_fid)
        return feedback

    def _index_feedback(self, feedback: MealFeedback, *, family_id: str | None = None) -> None:
        from mealprepper.index.preference_index import PreferenceIndex

        fid = self._fid(family_id)
        try:
            PreferenceIndex(
                db_path=self.db_path,
                settings=self.settings,
                family_id=fid,
            ).index_feedback(feedback)
        except Exception as exc:
            logger.warning("Failed to index feedback %s: %s", feedback.id, exc)

    def get_unapplied_feedback(self, *, family_id: str | None = None) -> list[MealFeedback]:
        fid = self._fid(family_id)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM meal_feedback
                WHERE applied = 0 AND family_id = ?
                ORDER BY created_at
                """,
                (fid,),
            ).fetchall()
        return [
            MealFeedback(
                id=r["id"],
                meal_title=r["meal_title"],
                meal_block=r["meal_block"] or "",
                day=r["day"] or "",
                rating=FeedbackRating(r["rating"]),
                comment=r["comment"] or "",
                member_id=r["member_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
                applied_to_preferences=bool(r["applied"]),
            )
            for r in rows
        ]

    def mark_feedback_applied(
        self,
        feedback_ids: list[str],
        *,
        family_id: str | None = None,
    ) -> None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            for feedback_id in feedback_ids:
                conn.execute(
                    "UPDATE meal_feedback SET applied = 1 WHERE id = ? AND family_id = ?",
                    (feedback_id, fid),
                )

    def get_preferences(self, *, family_id: str | None = None) -> PreferenceProfile:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT profile_json FROM preferences
                WHERE family_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (fid,),
            ).fetchone()
        if not row:
            return PreferenceProfile()
        return PreferenceProfile.model_validate(json.loads(row["profile_json"]))

    def save_preferences(
        self,
        profile: PreferenceProfile,
        *,
        family_id: str | None = None,
    ) -> None:
        fid = self._fid(family_id)
        pid = str(uuid.uuid4())
        now = _utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO preferences (id, family_id, profile_json, updated_at) VALUES (?, ?, ?, ?)",
                (pid, fid, json.dumps(profile.model_dump(mode="json")), now),
            )

    def save_preference_summary(
        self,
        summary_text: str,
        feedback_count: int = 0,
        *,
        family_id: str | None = None,
    ) -> None:
        fid = self._fid(family_id)
        pid = str(uuid.uuid4())
        now = _utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO preference_summaries (id, family_id, summary_text, feedback_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (pid, fid, summary_text, feedback_count, now),
            )

    def get_latest_preference_summary(self, *, family_id: str | None = None) -> str:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT summary_text FROM preference_summaries
                WHERE family_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (fid,),
            ).fetchone()
        return row["summary_text"] if row else ""

    def get_compact_preferences(self, *, family_id: str | None = None) -> PreferenceProfile:
        """Preference profile augmented with stored compressed summary in notes."""
        from mealprepper.context.compressor import ContextCompressor

        profile = self.get_preferences(family_id=family_id)
        summary = self.get_latest_preference_summary(family_id=family_id)
        if summary and summary not in profile.notes:
            profile.notes = ContextCompressor().merge_notes(profile.notes, summary)
        return ContextCompressor().compress_profile(profile)

    def create_approval_request(
        self,
        plan_id: str,
        message: str,
        *,
        family_id: str | None = None,
    ) -> str:
        fid = self._fid(family_id)
        rid = str(uuid.uuid4())
        now = _utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO approval_requests (id, family_id, weekly_plan_id, status, message, created_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (rid, fid, plan_id, message, now),
            )
        return rid

    def resolve_approval(
        self,
        request_id: str,
        approved: bool,
        response: str = "",
        *,
        family_id: str | None = None,
    ) -> None:
        fid = self._fid(family_id)
        status = "approved" if approved else "rejected"
        now = _utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE approval_requests
                SET status = ?, response = ?, resolved_at = ?
                WHERE id = ? AND family_id = ?
                """,
                (status, response, now, request_id, fid),
            )

    def get_pending_approval(self, *, family_id: str | None = None) -> dict | None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM approval_requests
                WHERE status = 'pending' AND family_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (fid,),
            ).fetchone()
        return dict(row) if row else None

    def save_saved_recipe(
        self,
        recipe: SavedRecipe,
        *,
        family_id: str | None = None,
    ) -> SavedRecipe:
        from mealprepper.index.recipe_index import RecipeIndex

        fid = self._fid(family_id)
        recipe_id = recipe.id or str(uuid.uuid4())
        now = _utcnow()
        recipe.id = recipe_id
        recipe.created_at = recipe.created_at or now
        recipe.updated_at = now
        RecipeIndex(
            db_path=self.db_path,
            settings=self.settings,
            family_id=fid,
        ).index_recipe(recipe)
        return recipe

    def get_saved_recipe(
        self,
        recipe_id: str,
        *,
        family_id: str | None = None,
    ) -> SavedRecipe | None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM recipe_repository WHERE id = ? AND family_id = ?",
                (recipe_id, fid),
            ).fetchone()
        if not row:
            return None
        return self._row_to_saved_recipe(row)

    def find_saved_recipe_by_hash(
        self,
        content_hash: str,
        *,
        family_id: str | None = None,
    ) -> SavedRecipe | None:
        fid = self._fid(family_id)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM recipe_repository
                WHERE content_hash = ? AND family_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (content_hash, fid),
            ).fetchone()
        if not row:
            return None
        return self._row_to_saved_recipe(row)

    def list_saved_recipes(
        self,
        limit: int = 50,
        *,
        family_id: str | None = None,
    ) -> list[SavedRecipe]:
        fid = self._fid(family_id)
        with self._conn() as conn:
            if limit <= 0:
                rows = conn.execute(
                    """
                    SELECT * FROM recipe_repository
                    WHERE family_id = ?
                    ORDER BY updated_at DESC
                    """,
                    (fid,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM recipe_repository
                    WHERE family_id = ?
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (fid, limit),
                ).fetchall()
        return [self._row_to_saved_recipe(row) for row in rows]

    def delete_saved_recipe(
        self,
        recipe_id: str,
        *,
        family_id: str | None = None,
    ) -> bool:
        fid = self._fid(family_id)
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM recipe_repository WHERE id = ? AND family_id = ?",
                (recipe_id, fid),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_saved_recipe(row: sqlite3.Row) -> SavedRecipe:
        recipe = None
        if row["recipe_json"]:
            recipe = MealRecipe.model_validate(json.loads(row["recipe_json"]))
        meal_blocks = [part for part in (row["meal_blocks"] or "").split(",") if part]
        tags = [part for part in (row["tags"] or "").split(",") if part]
        key_ingredients = []
        if recipe:
            key_ingredients = [ing.name for ing in recipe.ingredients[:8]]
        return SavedRecipe(
            id=row["id"],
            title=row["title"],
            source_type=row["source_type"],
            source_url=row["source_url"] or "",
            source_label=row["source_label"] or "",
            content_hash=row["content_hash"] or "",
            raw_text=row["raw_text"] or "",
            recipe=recipe,
            key_ingredients=key_ingredients,
            meal_blocks=meal_blocks,
            tags=tags,
            notes=row["notes"] or "",
            favorite=bool(row["favorite"]),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        )


def ensure_db_schema(db_path: Path, settings: Settings | None = None) -> None:
    """Idempotently create SQLite tables and FTS indexes for the given path."""
    resolved = db_path.resolve()
    if resolved in _schema_initialized:
        return
    SQLiteStore(db_path=db_path, settings=settings)
    _schema_initialized.add(resolved)

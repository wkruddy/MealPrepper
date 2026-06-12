from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mealprepper.config import Settings, get_settings
from mealprepper.models.meals import PlannedMeal
from mealprepper.models.plans import WeeklyPlan
from mealprepper.storage.migrations import DEFAULT_FAMILY_ID
from mealprepper.storage.sqlite import ensure_db_schema

logger = logging.getLogger(__name__)


@dataclass
class IndexedMeal:
    meal_id: str
    title: str
    meal_block: str
    day: str
    ingredients: str
    tags: str
    plan_id: str
    score: float = 0.0


class MealIndex:
    """SQLite FTS5 index for saved meals — retrieve top-k for meal_block queries."""

    def __init__(
        self,
        db_path: Path | None = None,
        settings: Settings | None = None,
        family_id: str = DEFAULT_FAMILY_ID,
    ) -> None:
        self.settings = settings or get_settings()
        self.db_path = db_path or self.settings.database_path
        self.family_id = family_id
        ensure_db_schema(self.db_path, self.settings)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def index_meal(
        self,
        meal: PlannedMeal,
        *,
        plan_id: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> str:
        meal_id = f"{plan_id}:{meal.day}:{meal.meal_block}:{meal.recipe.title}"
        ingredients = ", ".join(i.name for i in meal.recipe.ingredients)
        tags = ", ".join(meal.recipe.tags)
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO meal_index
                (meal_id, family_id, title, meal_block, day, ingredients, tags, plan_id, body)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meal_id,
                    self.family_id,
                    meal.recipe.title,
                    meal.meal_block,
                    meal.day,
                    ingredients,
                    tags,
                    plan_id,
                    self._meal_body(meal),
                ),
            )
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()
        return meal_id

    def index_plan(self, plan: WeeklyPlan) -> int:
        plan_id = plan.id or ""
        count = 0
        with self._connect() as conn:
            for meal in plan.meals:
                self.index_meal(meal, plan_id=plan_id, conn=conn)
                count += 1
            conn.commit()
        return count

    def search(
        self,
        query: str,
        *,
        meal_block: str | None = None,
        top_k: int = 5,
    ) -> list[IndexedMeal]:
        if not query.strip() and not meal_block:
            return self.recent(top_k=top_k, meal_block=meal_block)

        fts_query = self._fts_query(query)
        block_filter = " AND m.family_id = ?"
        params: list = [fts_query, self.family_id]
        if meal_block:
            block_filter += " AND m.meal_block = ?"
            params.append(meal_block)
        params.append(top_k)

        sql = f"""
            SELECT m.meal_id, m.title, m.meal_block, m.day, m.ingredients, m.tags, m.plan_id,
                   bm25(meal_index_fts) AS score
            FROM meal_index_fts f
            JOIN meal_index m ON m.rowid = f.rowid
            WHERE meal_index_fts MATCH ?
            {block_filter}
            ORDER BY score
            LIMIT ?
        """
        with self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                logger.debug("Meal FTS search failed (%s), falling back to LIKE", exc)
                return self._like_search(query, meal_block=meal_block, top_k=top_k)

        return [self._row_to_meal(r) for r in rows]

    def recent(self, *, top_k: int = 5, meal_block: str | None = None) -> list[IndexedMeal]:
        sql = """
            SELECT meal_id, title, meal_block, day, ingredients, tags, plan_id, 0 AS score
            FROM meal_index
            WHERE family_id = ?
        """
        params: list = [self.family_id]
        if meal_block:
            sql += " AND meal_block = ?"
            params.append(meal_block)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(top_k)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_meal(r) for r in rows]

    def format_for_prompt(self, meals: list[IndexedMeal]) -> str:
        if not meals:
            return "No relevant past meals indexed yet."
        lines = []
        for m in meals:
            lines.append(
                f"- {m.title} ({m.meal_block}, {m.day}): {m.ingredients[:80]}"
            )
        return "\n".join(lines)

    def _meal_body(self, meal: PlannedMeal) -> str:
        r = meal.recipe
        parts = [r.title, r.description, meal.meal_block, meal.day]
        parts.extend(i.name for i in r.ingredients)
        parts.extend(r.tags)
        return " ".join(p for p in parts if p)

    def _fts_query(self, query: str) -> str:
        tokens = [t.strip('"') for t in query.replace(",", " ").split() if t.strip()]
        if not tokens:
            return "*"
        return " OR ".join(f'"{t}"' for t in tokens[:8])

    def _like_search(
        self,
        query: str,
        *,
        meal_block: str | None,
        top_k: int,
    ) -> list[IndexedMeal]:
        pattern = f"%{query}%"
        sql = """
            SELECT meal_id, title, meal_block, day, ingredients, tags, plan_id, 0 AS score
            FROM meal_index
            WHERE family_id = ? AND (title LIKE ? OR ingredients LIKE ? OR body LIKE ?)
        """
        params: list = [self.family_id, pattern, pattern, pattern]
        if meal_block:
            sql += " AND meal_block = ?"
            params.append(meal_block)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(top_k)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_meal(r) for r in rows]

    @staticmethod
    def _row_to_meal(row: sqlite3.Row) -> IndexedMeal:
        return IndexedMeal(
            meal_id=row["meal_id"],
            title=row["title"],
            meal_block=row["meal_block"],
            day=row["day"],
            ingredients=row["ingredients"] or "",
            tags=row["tags"] or "",
            plan_id=row["plan_id"] or "",
            score=float(row["score"] or 0),
        )

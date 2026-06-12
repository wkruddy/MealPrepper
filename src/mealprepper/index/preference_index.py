from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mealprepper.config import Settings, get_settings
from mealprepper.models.feedback import FeedbackRating, MealFeedback
from mealprepper.storage.migrations import DEFAULT_FAMILY_ID
from mealprepper.storage.sqlite import ensure_db_schema

logger = logging.getLogger(__name__)

POSITIVE = {FeedbackRating.LOVED, FeedbackRating.LIKED}
NEGATIVE = {FeedbackRating.DISLIKED, FeedbackRating.REJECT}


@dataclass
class IndexedFeedback:
    feedback_id: str
    meal_title: str
    meal_block: str
    rating: str
    comment: str
    score: float = 0.0


class PreferenceIndex:
    """Index feedback entries; retrieve relevant likes/dislikes for meal block or query."""

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

    def index_feedback(self, feedback: MealFeedback, conn: sqlite3.Connection | None = None) -> None:
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        try:
            body = " ".join(
                p for p in [feedback.meal_title, feedback.meal_block, feedback.comment, feedback.rating.value] if p
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO feedback_index
                (feedback_id, family_id, meal_title, meal_block, rating, comment, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback.id or "",
                    self.family_id,
                    feedback.meal_title,
                    feedback.meal_block or "",
                    feedback.rating.value,
                    feedback.comment or "",
                    body,
                ),
            )
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()

    def search(
        self,
        query: str = "",
        *,
        meal_block: str | None = None,
        top_k: int = 10,
    ) -> list[IndexedFeedback]:
        if query.strip():
            fts_query = " OR ".join(f'"{t}"' for t in query.split()[:6])
            block_filter = " AND f.family_id = ?"
            params: list = [fts_query, self.family_id]
            if meal_block:
                block_filter += " AND f.meal_block = ?"
                params.append(meal_block)
            params.append(top_k)
            sql = f"""
                SELECT f.feedback_id, f.meal_title, f.meal_block, f.rating, f.comment,
                       bm25(feedback_index_fts) AS score
                FROM feedback_index_fts fts
                JOIN feedback_index f ON f.rowid = fts.rowid
                WHERE feedback_index_fts MATCH ?
                {block_filter}
                ORDER BY score
                LIMIT ?
            """
            with self._connect() as conn:
                try:
                    rows = conn.execute(sql, params).fetchall()
                    return [self._row_to_feedback(r) for r in rows]
                except sqlite3.OperationalError:
                    pass

        return self._filter_by_block(meal_block, top_k=top_k)

    def relevant_for_block(self, meal_block: str, top_k: int = 8) -> str:
        """Compact prompt text: likes/dislikes relevant to a meal block."""
        items = self._filter_by_block(meal_block, top_k=top_k)
        if not items:
            items = self._filter_by_block(None, top_k=top_k)
        if not items:
            return ""

        liked: list[str] = []
        disliked: list[str] = []
        for fb in items:
            if fb.rating in ("loved", "liked"):
                liked.append(fb.meal_title)
            elif fb.rating in ("disliked", "reject"):
                disliked.append(fb.meal_title)

        lines = []
        if liked:
            lines.append(f"Liked (from feedback): {', '.join(dict.fromkeys(liked[:6]))}")
        if disliked:
            lines.append(f"Disliked (from feedback): {', '.join(dict.fromkeys(disliked[:6]))}")
        return "\n".join(lines)

    def _filter_by_block(self, meal_block: str | None, top_k: int) -> list[IndexedFeedback]:
        if meal_block:
            sql = """
                SELECT feedback_id, meal_title, meal_block, rating, comment, 0 AS score
                FROM feedback_index
                WHERE family_id = ? AND (meal_block = ? OR meal_block = '')
                ORDER BY rowid DESC LIMIT ?
            """
            params: list = [self.family_id, meal_block, top_k]
        else:
            sql = """
                SELECT feedback_id, meal_title, meal_block, rating, comment, 0 AS score
                FROM feedback_index
                WHERE family_id = ?
                ORDER BY rowid DESC LIMIT ?
            """
            params = [self.family_id, top_k]
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_feedback(r) for r in rows]

    @staticmethod
    def _row_to_feedback(row: sqlite3.Row) -> IndexedFeedback:
        return IndexedFeedback(
            feedback_id=row["feedback_id"],
            meal_title=row["meal_title"],
            meal_block=row["meal_block"] or "",
            rating=row["rating"],
            comment=row["comment"] or "",
            score=float(row["score"] or 0),
        )

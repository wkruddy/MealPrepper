from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mealprepper.config import Settings, get_settings
from mealprepper.context.compressor import ContextCompressor
from mealprepper.models.plans import WeeklyPlan
from mealprepper.storage.migrations import DEFAULT_FAMILY_ID
from mealprepper.storage.sqlite import ensure_db_schema

logger = logging.getLogger(__name__)


@dataclass
class IndexedPlan:
    plan_id: str
    week_start: str
    week_end: str
    status: str
    summary: str
    score: float = 0.0


class PlanIndex:
    """Index past weekly plans; retrieve similar weeks or recent summaries."""

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
        self._compressor = ContextCompressor()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def index_plan(self, plan: WeeklyPlan, conn: sqlite3.Connection | None = None) -> None:
        plan_id = plan.id or ""
        summary = self._compressor.summarize_plan(plan)
        body = f"{summary} {plan.synergy_notes or ''}"
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO plan_index
                (plan_id, family_id, week_start, week_end, status, summary, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    self.family_id,
                    plan.week_start.isoformat(),
                    plan.week_end.isoformat(),
                    plan.status.value,
                    summary,
                    body,
                ),
            )
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()

    def search(self, query: str, *, top_k: int = 3) -> list[IndexedPlan]:
        fts_query = " OR ".join(f'"{t}"' for t in query.replace(",", " ").split()[:6]) or "*"
        sql = """
            SELECT p.plan_id, p.week_start, p.week_end, p.status, p.summary,
                   bm25(plan_index_fts) AS score
            FROM plan_index_fts fts
            JOIN plan_index p ON p.rowid = fts.rowid
            WHERE plan_index_fts MATCH ? AND p.family_id = ?
            ORDER BY score
            LIMIT ?
        """
        with self._connect() as conn:
            try:
                rows = conn.execute(sql, (fts_query, self.family_id, top_k)).fetchall()
                return [self._row_to_plan(r) for r in rows]
            except sqlite3.OperationalError:
                return self.recent(top_k=top_k)

    def recent(self, top_k: int = 3) -> list[IndexedPlan]:
        sql = """
            SELECT plan_id, week_start, week_end, status, summary, 0 AS score
            FROM plan_index
            WHERE family_id = ?
            ORDER BY week_start DESC LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (self.family_id, top_k)).fetchall()
        return [self._row_to_plan(r) for r in rows]

    def similar_to_week(self, week_start: date, query_ingredients: str = "", top_k: int = 2) -> str:
        """Compact text of similar/recent past weeks for prompt context."""
        if query_ingredients.strip():
            plans = self.search(query_ingredients, top_k=top_k)
        else:
            plans = self.recent(top_k=top_k)

        if not plans:
            return ""

        lines = ["Recent / similar past weeks:"]
        for p in plans:
            if p.week_start == week_start.isoformat():
                continue
            lines.append(f"- {p.summary.split(chr(10))[0]}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> IndexedPlan:
        return IndexedPlan(
            plan_id=row["plan_id"],
            week_start=row["week_start"],
            week_end=row["week_end"],
            status=row["status"],
            summary=row["summary"] or "",
            score=float(row["score"] or 0),
        )

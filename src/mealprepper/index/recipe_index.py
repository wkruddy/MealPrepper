from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mealprepper.config import Settings, get_settings
from mealprepper.models.recipe_repository import SavedRecipe
from mealprepper.storage.sqlite import ensure_db_schema

logger = logging.getLogger(__name__)


@dataclass
class IndexedRecipe:
    recipe_id: str
    title: str
    meal_blocks: str
    ingredients: str
    tags: str
    source_type: str
    source_label: str
    notes: str
    score: float = 0.0


class RecipeIndex:
    """FTS5 index for the family recipe repository."""

    def __init__(self, db_path: Path | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = db_path or self.settings.database_path
        ensure_db_schema(self.db_path, self.settings)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def index_recipe(
        self,
        recipe: SavedRecipe,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        recipe_id = recipe.id or ""
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        created = recipe.created_at.isoformat() if recipe.created_at else now
        updated = recipe.updated_at.isoformat() if recipe.updated_at else now
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO recipe_repository
                (id, title, source_type, source_url, source_label, content_hash, raw_text,
                 recipe_json, meal_blocks, tags, notes, favorite, body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipe_id,
                    recipe.title,
                    recipe.source_type,
                    recipe.source_url,
                    recipe.source_label,
                    recipe.content_hash,
                    recipe.raw_text,
                    recipe.recipe.model_dump_json() if recipe.recipe else None,
                    ",".join(recipe.meal_blocks),
                    ",".join(recipe.tags),
                    recipe.notes,
                    1 if recipe.favorite else 0,
                    self._recipe_body(recipe),
                    created,
                    updated,
                ),
            )
            if own_conn:
                conn.commit()
        finally:
            if own_conn:
                conn.close()
        return recipe_id

    def search(
        self,
        query: str,
        *,
        meal_block: str | None = None,
        top_k: int = 5,
    ) -> list[IndexedRecipe]:
        if not query.strip() and not meal_block:
            return self.recent(top_k=top_k, meal_block=meal_block)

        fts_query = self._fts_query(query)
        block_filter = ""
        params: list = [fts_query]
        if meal_block:
            block_filter = " AND (r.meal_blocks LIKE ? OR r.meal_blocks = '')"
            params.append(f"%{meal_block}%")
        params.append(top_k)

        sql = f"""
            SELECT r.id, r.title, r.meal_blocks, r.tags, r.source_type, r.source_label, r.notes,
                   bm25(recipe_repository_fts) AS score
            FROM recipe_repository_fts f
            JOIN recipe_repository r ON r.rowid = f.rowid
            WHERE recipe_repository_fts MATCH ?
            {block_filter}
            ORDER BY score
            LIMIT ?
        """
        with self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError as exc:
                logger.debug("Recipe FTS search failed (%s), falling back to LIKE", exc)
                return self._like_search(query, meal_block=meal_block, top_k=top_k)

        return [self._row_to_recipe(row) for row in rows]

    def recent(self, *, top_k: int = 5, meal_block: str | None = None) -> list[IndexedRecipe]:
        sql = """
            SELECT id, title, meal_blocks, tags, source_type, source_label, notes, 0 AS score
            FROM recipe_repository
            WHERE favorite = 1
        """
        params: list = []
        if meal_block:
            sql += " AND (meal_blocks LIKE ? OR meal_blocks = '')"
            params.append(f"%{meal_block}%")
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(top_k)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def format_for_prompt(self, recipes: list[IndexedRecipe]) -> str:
        if not recipes:
            return "No saved family recipes yet."
        lines = []
        for recipe in recipes:
            label = recipe.source_label or recipe.source_type
            detail = recipe.notes[:120] if recipe.notes else recipe.tags
            lines.append(f"- {recipe.title} ({label}): {detail}".strip())
        return "\n".join(lines)

    def _recipe_body(self, recipe: SavedRecipe) -> str:
        parts = [recipe.title, recipe.notes, recipe.source_label, recipe.source_url]
        parts.extend(recipe.tags)
        parts.extend(recipe.meal_blocks)
        parts.extend(recipe.key_ingredients)
        if recipe.recipe:
            parts.append(recipe.recipe.description)
            parts.extend(i.name for i in recipe.recipe.ingredients)
            parts.extend(recipe.recipe.tags)
        parts.append(recipe.raw_text[:2000])
        return " ".join(part for part in parts if part)

    def _fts_query(self, query: str) -> str:
        tokens = [token.strip('"') for token in query.replace(",", " ").split() if token.strip()]
        if not tokens:
            return "*"
        return " OR ".join(f'"{token}"' for token in tokens[:8])

    def _like_search(
        self,
        query: str,
        *,
        meal_block: str | None,
        top_k: int,
    ) -> list[IndexedRecipe]:
        pattern = f"%{query}%"
        sql = """
            SELECT id, title, meal_blocks, tags, source_type, source_label, notes, 0 AS score
            FROM recipe_repository
            WHERE favorite = 1 AND (title LIKE ? OR notes LIKE ? OR body LIKE ? OR tags LIKE ?)
        """
        params: list = [pattern, pattern, pattern, pattern]
        if meal_block:
            sql += " AND (meal_blocks LIKE ? OR meal_blocks = '')"
            params.append(f"%{meal_block}%")
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(top_k)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_recipe(row) for row in rows]

    @staticmethod
    def _row_to_recipe(row: sqlite3.Row) -> IndexedRecipe:
        return IndexedRecipe(
            recipe_id=row["id"],
            title=row["title"],
            meal_blocks=row["meal_blocks"] or "",
            ingredients="",
            tags=row["tags"] or "",
            source_type=row["source_type"] or "",
            source_label=row["source_label"] or "",
            notes=row["notes"] or "",
            score=float(row["score"] or 0),
        )
